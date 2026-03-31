#!/usr/bin/env bash
set -euo pipefail
set -o errtrace  # ensure ERR traps fire inside functions/subshells

########################################
# CONFIGURATION
########################################

ENV="dev"   # dev / test / prod
SOURCE_TAG="dev"

PROJECT="a083gt"
PROJECT_ID="${PROJECT}-${ENV}"
ARTIFACT_REGISTRY_PROJECT="c4hnrd-tools"
OC_NAMESPACE="cbaab0-${ENV}"
IMPORTER_SECRET="namex-solr-importer-${ENV}-secret"

LEADER_BACKEND="namex-solr-leader-backend"
FOLLOWER_BACKEND="namex-solr-follower-backend"

ZONES=("northamerica-northeast1-a" "northamerica-northeast1-b" "northamerica-northeast1-c")
REGION="northamerica-northeast1"
REPO_PATH="${REGION}-docker.pkg.dev/${ARTIFACT_REGISTRY_PROJECT}/vm-repo"

# Template version must match what update-solr-base-image.sh created
TEMPLATE_VERSION=""

LEADER_TEMPLATE="namex-solr-leader-vm-tmpl-${ENV}${TEMPLATE_VERSION:+-$TEMPLATE_VERSION}"
FOLLOWER_TEMPLATE="namex-solr-follower-vm-tmpl-${ENV}${TEMPLATE_VERSION:+-$TEMPLATE_VERSION}"


########################################
# HELPER FUNCTIONS
########################################

log() { echo -e "\n[$(date -u +%H:%M:%S)] 🔹  $1\n"; }

require() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: Missing required command: $1"
        exit 1
    fi
}

check_prereqs() {
    log "Checking required tools…"
    require gcloud
    require docker
    require make
    require oc

    log "Verifying authentication…"
    if ! gcloud auth print-access-token &>/dev/null; then
        echo "ERROR: gcloud is not authenticated. Run: gcloud auth login"
        exit 1
    fi
    if ! oc whoami &>/dev/null; then
        echo "ERROR: oc is not authenticated. Run: oc login"
        exit 1
    fi
}

create_vm_in_available_zone() {
    local vm_name="$1"
    local template="$2"

    for zone in "${ZONES[@]}"; do
        log "Trying to create ${vm_name} in ${zone}…" >&2
        if timeout 120 gcloud compute instances create "${vm_name}" \
            --source-instance-template "${template}" \
            --zone "${zone}" \
            --project "${PROJECT_ID}" >&2; then
            echo "${zone}"
            return 0
        fi
        log "Zone ${zone} unavailable, trying next…" >&2
    done

    echo "ERROR: All zones exhausted. Could not create ${vm_name}." >&2
    exit 1
}

ensure_instance_group() {
    local group_name="$1"
    local zone="$2"

    if ! gcloud compute instance-groups unmanaged describe "${group_name}" \
        --zone "${zone}" --project "${PROJECT_ID}" &>/dev/null; then
        log "Creating instance group ${group_name} in ${zone}…"
        gcloud compute instance-groups unmanaged create "${group_name}" \
            --zone "${zone}" --project "${PROJECT_ID}"
        gcloud compute instance-groups unmanaged set-named-ports "${group_name}" \
            --zone "${zone}" --project "${PROJECT_ID}" \
            --named-ports=http:8983
    fi
}

add_to_backend() {
    local backend_name="$1"
    local group="$2"
    local zone="$3"
    log "Adding instance group ${group} (${zone}) to backend ${backend_name}…"
    gcloud compute backend-services add-backend "${backend_name}" \
        --instance-group="${group}" \
        --instance-group-zone="${zone}" \
        --region="${REGION}" \
        --project="${PROJECT_ID}" 2>/dev/null || true
}

remove_old_backend() {
    local backend_name="$1"
    local new_group="$2"
    local new_zone="$3"
    local old_group="${4:-}"
    local old_zone="${5:-}"

    if [[ -n "${old_group}" && -n "${old_zone}" ]]; then
        if [[ "${old_group}" != "${new_group}" || "${old_zone}" != "${new_zone}" ]]; then
            if instance_group_exists "${old_group}" "${old_zone}"; then
                log "Removing old instance group ${old_group} (${old_zone}) from backend ${backend_name}…"
                gcloud compute backend-services remove-backend "${backend_name}" \
                    --instance-group="${old_group}" \
                    --instance-group-zone="${old_zone}" \
                    --region="${REGION}" \
                    --project="${PROJECT_ID}"
            else
                log "Old instance group ${old_group} does not exist in ${old_zone}, skipping removal."
            fi
        fi
    fi
}

rollback_backend() {
    local backend_name="$1"
    local group_name="$2"
    local zone="$3"
    log "ROLLBACK: Removing ${group_name} (${zone}) from backend ${backend_name}…"
    gcloud compute backend-services remove-backend "${backend_name}" \
        --instance-group="${group_name}" \
        --instance-group-zone="${zone}" \
        --region="${REGION}" \
        --project="${PROJECT_ID}" 2>/dev/null || true
}

get_instance_zone() {
    local vm_name="$1"
    gcloud compute instances list \
        --filter "name=${vm_name}" \
        --format "value(zone)" \
        --project "${PROJECT_ID}" | xargs basename
}

instance_group_exists() {
    local group_name="$1"
    local zone="$2"
    gcloud compute instance-groups unmanaged describe "${group_name}" \
        --zone="${zone}" --project="${PROJECT_ID}" &>/dev/null
}

retry() {
    local max_attempts="${1}"
    shift
    for i in $(seq 1 "${max_attempts}"); do
        if "$@"; then
            return 0
        fi
        log "Attempt ${i}/${max_attempts} failed, retrying in 5s…"
        sleep 5
    done
    echo "ERROR: Command failed after ${max_attempts} attempts: $*"
    return 1
}

wait_for_solr_ready() {
    local vm_name="$1"
    local zone="$2"
    local core="${3:-name_request}"
    local max_attempts="${4:-60}"
    log "Waiting for Solr core ${core} to be ready on ${vm_name}…"
    for i in $(seq 1 "${max_attempts}"); do
        if gcloud compute ssh "${vm_name}" \
            --zone="${zone}" --project="${PROJECT_ID}" \
            --tunnel-through-iap \
            --command="curl -sf http://localhost:8983/solr/${core}/admin/ping" \
            >/dev/null 2>&1; then
            log "Solr core ${core} is ready on ${vm_name}."
            return 0
        fi
        sleep 10
    done
    echo "ERROR: Solr core ${core} not ready on ${vm_name} after ${max_attempts} attempts."
    return 1
}

wait_for_healthy_backend() {
    local backend_name="$1"
    local instance_name="$2"
    local max_attempts="${3:-30}"
    log "Waiting for ${instance_name} to be healthy in backend ${backend_name}…"
    for i in $(seq 1 "${max_attempts}"); do
        local health
        health=$(gcloud compute backend-services get-health "${backend_name}" \
            --region="${REGION}" \
            --project="${PROJECT_ID}" \
            --flatten="status.healthStatus[]" \
            --format="csv[no-heading](status.healthStatus.instance,status.healthStatus.healthState)" \
            2>/dev/null || true)
        if echo "${health}" | grep "/instances/${instance_name}," | grep -q "HEALTHY"; then
            log "Instance ${instance_name} is healthy in backend ${backend_name}."
            return 0
        fi
        sleep 10
    done
    echo "ERROR: ${instance_name} did not become healthy in ${backend_name} after ${max_attempts} attempts."
    return 1
}

reset_reindex_flag() {
    log "Resetting REINDEX_CORE in secret…"
    oc -n "${OC_NAMESPACE}" patch secret "${IMPORTER_SECRET}" \
        -p '{"stringData":{"REINDEX_CORE":"False"}}' 2>/dev/null || true
}

########################################
# BUILD DOCKER IMAGES (DEV ONLY)
########################################
build_images() {

    log "Building local Solr images…"
    cd ../namex-solr
    make build

    log "Authenticating Docker to GCP Artifact Registry…"
    gcloud auth configure-docker "${REGION}-docker.pkg.dev"

    log "Tagging images…"

    docker tag name-request-solr-leader "${REPO_PATH}/name-request-solr-leader:${ENV}"
    docker tag name-request-solr-follower "${REPO_PATH}/name-request-solr-follower:${ENV}"

    log "Pushing images…"
    docker push "${REPO_PATH}/name-request-solr-leader:${ENV}"
    docker push "${REPO_PATH}/name-request-solr-follower:${ENV}"
}

########################################
# TAGGING IMAGES FOR TEST/PROD
########################################
tag_images() {
    log "Tagging ${SOURCE_TAG} → ${ENV}…"

    gcloud artifacts docker tags add \
        "${REPO_PATH}/name-request-solr-leader:${SOURCE_TAG}" \
        "${REPO_PATH}/name-request-solr-leader:${ENV}"

    # Keep follower tagging for TEST/PROD — required for full deploy
    gcloud artifacts docker tags add \
        "${REPO_PATH}/name-request-solr-follower:${SOURCE_TAG}" \
        "${REPO_PATH}/name-request-solr-follower:${ENV}"
}

########################################
# DEPLOY NEW INSTANCES
########################################
deploy_instances() {

    timestamp=$(date -u +"%Y-%m-%d--%H%M%S")

    NEW_LEADER_VM="namex-solr-leader-${ENV}-${timestamp}"
    NEW_FOLLOWER_VM="namex-solr-follower-${ENV}-${timestamp}"

    # --- Issue 5: Safe old VM detection (sort + limit 1) ---
    log "Determining old leader & follower VMs…"
    OLD_LEADER_VM=$(gcloud compute instances list \
        --format="value(name)" \
        --filter="name~'^namex-solr-leader-${ENV}-'" \
        --sort-by="~creationTimestamp" \
        --limit=1 \
        --project="${PROJECT_ID}" || true)

    OLD_FOLLOWER_VM=$(gcloud compute instances list \
        --format="value(name)" \
        --filter="name~'^namex-solr-follower-${ENV}-'" \
        --sort-by="~creationTimestamp" \
        --limit=1 \
        --project="${PROJECT_ID}" || true)

    log "OLD_LEADER_VM=${OLD_LEADER_VM:-none}"
    log "OLD_FOLLOWER_VM=${OLD_FOLLOWER_VM:-none}"

    # Resolve old zones up front
    OLD_LEADER_ZONE=""
    if [[ -n "${OLD_LEADER_VM}" ]]; then
        OLD_LEADER_ZONE=$(get_instance_zone "${OLD_LEADER_VM}")
    fi
    OLD_FOLLOWER_ZONE=""
    if [[ -n "${OLD_FOLLOWER_VM}" ]]; then
        OLD_FOLLOWER_ZONE=$(get_instance_zone "${OLD_FOLLOWER_VM}")
    fi

    #####################################
    # CREATE NEW LEADER
    #####################################

    # Ensure connection draining is set to avoid cutting active requests during swap
    log "Ensuring connection draining on backend services…"
    gcloud compute backend-services update "${LEADER_BACKEND}" \
        --connection-draining-timeout=30 \
        --region="${REGION}" --project="${PROJECT_ID}" 2>/dev/null || true
    gcloud compute backend-services update "${FOLLOWER_BACKEND}" \
        --connection-draining-timeout=30 \
        --region="${REGION}" --project="${PROJECT_ID}" 2>/dev/null || true

    log "Creating NEW Leader VM: ${NEW_LEADER_VM}"
    LEADER_ZONE=$(create_vm_in_available_zone "${NEW_LEADER_VM}" "${LEADER_TEMPLATE}")
    log "Leader created in zone: ${LEADER_ZONE}"

    NEW_LEADER_INTERNAL_IP=$(gcloud compute instances describe "${NEW_LEADER_VM}" \
        --zone "${LEADER_ZONE}" --project "${PROJECT_ID}" \
        --format='value(networkInterfaces[0].networkIP)')

    # --- Wait for Solr core to be ready before any operations ---
    wait_for_solr_ready "${NEW_LEADER_VM}" "${LEADER_ZONE}" "name_request"

    # --- Zone-specific instance group names to avoid cross-zone collisions ---
    LEADER_ZONE_SUFFIX=$(basename "${LEADER_ZONE}" | grep -o '[a-c]$')
    NEW_LEADER_GRP="namex-solr-leader-grp-${ENV}-${LEADER_ZONE_SUFFIX}"
    OLD_LEADER_GRP=""
    if [[ -n "${OLD_LEADER_ZONE}" ]]; then
        OLD_LEADER_ZONE_SUFFIX=$(basename "${OLD_LEADER_ZONE}" | grep -o '[a-c]$')
        OLD_LEADER_GRP="namex-solr-leader-grp-${ENV}-${OLD_LEADER_ZONE_SUFFIX}"
    fi

    log "Adding NEW leader to instance group ${NEW_LEADER_GRP}…"
    ensure_instance_group "${NEW_LEADER_GRP}" "${LEADER_ZONE}"
    gcloud compute instance-groups unmanaged add-instances \
        "${NEW_LEADER_GRP}" \
        --zone "${LEADER_ZONE}" \
        --instances "${NEW_LEADER_VM}" \
        --project "${PROJECT_ID}"

    # Add new to backend (old still serves traffic during transition)
    add_to_backend "${LEADER_BACKEND}" "${NEW_LEADER_GRP}" "${LEADER_ZONE}"

    # --- Wait for new leader to be healthy BEFORE removing old ---
    if ! wait_for_healthy_backend "${LEADER_BACKEND}" "${NEW_LEADER_VM}"; then
        rollback_backend "${LEADER_BACKEND}" "${NEW_LEADER_GRP}" "${LEADER_ZONE}"
        log "Cleaning up failed leader VM: ${NEW_LEADER_VM}"
        gcloud compute instances delete "${NEW_LEADER_VM}" --zone="${LEADER_ZONE}" --project="${PROJECT_ID}" --quiet 2>/dev/null || true
        exit 1
    fi

    # New is healthy → safe to remove old backend
    remove_old_backend "${LEADER_BACKEND}" \
        "${NEW_LEADER_GRP}" "${LEADER_ZONE}" "${OLD_LEADER_GRP}" "${OLD_LEADER_ZONE}"

    # Same-zone case: old VM still in shared IG → remove it so import doesn't split
    if [[ -n "${OLD_LEADER_VM}" && "${OLD_LEADER_GRP}" == "${NEW_LEADER_GRP}" ]]; then
        log "Removing old leader ${OLD_LEADER_VM} from shared instance group ${NEW_LEADER_GRP}…"
        gcloud compute instance-groups unmanaged remove-instances "${NEW_LEADER_GRP}" \
            --zone="${LEADER_ZONE}" --instances="${OLD_LEADER_VM}" \
            --project="${PROJECT_ID}" 2>/dev/null || true
    fi

    # --- Trap ensures REINDEX_CORE resets even on failure (idempotent) ---
    trap reset_reindex_flag EXIT

    log "Enabling REINDEX_CORE in secret…"
    oc -n "${OC_NAMESPACE}" patch secret "${IMPORTER_SECRET}" \
        -p '{"stringData":{"REINDEX_CORE":"True"}}'

    # --- OC job idempotency guard ---
    JOB_NAME="namex-solr-importer-${ENV}-deploy-${timestamp}"
    oc -n "${OC_NAMESPACE}" delete job "${JOB_NAME}" --ignore-not-found=true

    log "Triggering importer CronJob…"
    oc -n "${OC_NAMESPACE}" create job \
        --from=cronjob/namex-solr-importer-"${ENV}" \
        "${JOB_NAME}"

    log "Waiting for importer job to complete (up to 90 min)…"
    JOB_DONE=""
    for _attempt in $(seq 1 540); do
        JOB_STATUS=$(oc -n "${OC_NAMESPACE}" get "job/${JOB_NAME}" \
            -o jsonpath='{.status.conditions[?(@.status=="True")].type}' 2>/dev/null || true)
        if echo "${JOB_STATUS}" | grep -q "Complete"; then
            JOB_DONE="complete"
            break
        fi
        if echo "${JOB_STATUS}" | grep -q "Failed"; then
            JOB_DONE="failed"
            break
        fi
        sleep 10
    done

    if [[ "${JOB_DONE}" != "complete" ]]; then
        echo "ERROR: Importer job ${JOB_DONE:-timed out}. Status: ${JOB_STATUS}"
        oc -n "${OC_NAMESPACE}" logs "job/${JOB_NAME}" --tail=30 2>/dev/null || true
        exit 1
    fi

    log "Importer job completed successfully."
    reset_reindex_flag

    ########################################
    # DEV ENV → LEADER ONLY
    ########################################
    if [[ "${ENV}" == "dev" ]]; then
        log "DEV environment: follower instance not required. Skipping follower creation."

        # --- Issue 9: Only delete old after everything succeeds ---
        if [[ -n "${OLD_LEADER_VM}" ]]; then
            log "Deleting OLD leader: ${OLD_LEADER_VM} (zone: ${OLD_LEADER_ZONE})"
            gcloud compute instances delete "${OLD_LEADER_VM}" --zone="${OLD_LEADER_ZONE}" --project="${PROJECT_ID}" --quiet
        fi

        log "Deployment complete (DEV: leader only)."
        return
    fi

    ########################################
    # TEST/PROD DEPLOY → CREATE FOLLOWER
    ########################################

    log "Creating NEW Follower VM: ${NEW_FOLLOWER_VM}"
    FOLLOWER_ZONE=$(create_vm_in_available_zone "${NEW_FOLLOWER_VM}" "${FOLLOWER_TEMPLATE}")
    log "Follower created in zone: ${FOLLOWER_ZONE}"

    # --- Wait for follower Solr core before configuring replication ---
    wait_for_solr_ready "${NEW_FOLLOWER_VM}" "${FOLLOWER_ZONE}" "name_request_follower"

    log "Setting follower replication properties…"
    retry 5 gcloud compute ssh "${NEW_FOLLOWER_VM}" \
      --zone="${FOLLOWER_ZONE}" --project="${PROJECT_ID}" \
      --tunnel-through-iap \
      --command="curl -sf -X POST -H 'Content-type: application/json' \
        -d '{\"set-user-property\":{\"solr.leaderUrl\": \"http://${NEW_LEADER_INTERNAL_IP}:8983/solr/name_request\"}}' \
        'http://localhost:8983/solr/name_request_follower/config/requestHandler?componentName=/replication'"

    retry 5 gcloud compute ssh "${NEW_FOLLOWER_VM}" \
      --zone="${FOLLOWER_ZONE}" --project="${PROJECT_ID}" \
      --tunnel-through-iap \
      --command="curl -sf -X POST -H 'Content-type: application/json' \
        -d '{\"set-user-property\":{\"solr.leaderUrl\": \"http://${NEW_LEADER_INTERNAL_IP}:8983/solr/name_request\"}}' \
        'http://localhost:8983/solr/name_request_follower/config/requestHandler'"

    # --- Zone-specific follower instance group ---
    FOLLOWER_ZONE_SUFFIX=$(basename "${FOLLOWER_ZONE}" | grep -o '[a-c]$')
    NEW_FOLLOWER_GRP="namex-solr-follower-grp-${ENV}-${FOLLOWER_ZONE_SUFFIX}"
    OLD_FOLLOWER_GRP=""
    if [[ -n "${OLD_FOLLOWER_ZONE}" ]]; then
        OLD_FOLLOWER_ZONE_SUFFIX=$(basename "${OLD_FOLLOWER_ZONE}" | grep -o '[a-c]$')
        OLD_FOLLOWER_GRP="namex-solr-follower-grp-${ENV}-${OLD_FOLLOWER_ZONE_SUFFIX}"
    fi

    log "Adding follower to instance group ${NEW_FOLLOWER_GRP}…"
    ensure_instance_group "${NEW_FOLLOWER_GRP}" "${FOLLOWER_ZONE}"
    gcloud compute instance-groups unmanaged add-instances \
        "${NEW_FOLLOWER_GRP}" \
        --zone "${FOLLOWER_ZONE}" \
        --instances "${NEW_FOLLOWER_VM}" \
        --project "${PROJECT_ID}"

    # Add new to backend (old still serves traffic during transition)
    add_to_backend "${FOLLOWER_BACKEND}" "${NEW_FOLLOWER_GRP}" "${FOLLOWER_ZONE}"

    # --- Wait for new follower to be healthy BEFORE removing old ---
    if ! wait_for_healthy_backend "${FOLLOWER_BACKEND}" "${NEW_FOLLOWER_VM}"; then
        rollback_backend "${FOLLOWER_BACKEND}" "${NEW_FOLLOWER_GRP}" "${FOLLOWER_ZONE}"
        log "Cleaning up failed follower VM: ${NEW_FOLLOWER_VM}"
        gcloud compute instances delete "${NEW_FOLLOWER_VM}" --zone="${FOLLOWER_ZONE}" --project="${PROJECT_ID}" --quiet 2>/dev/null || true
        exit 1
    fi

    # New is healthy → safe to remove old backend
    remove_old_backend "${FOLLOWER_BACKEND}" \
        "${NEW_FOLLOWER_GRP}" "${FOLLOWER_ZONE}" "${OLD_FOLLOWER_GRP}" "${OLD_FOLLOWER_ZONE}"

    # Same-zone case: old VM still in shared IG → remove it
    if [[ -n "${OLD_FOLLOWER_VM}" && "${OLD_FOLLOWER_GRP}" == "${NEW_FOLLOWER_GRP}" ]]; then
        log "Removing old follower ${OLD_FOLLOWER_VM} from shared instance group ${NEW_FOLLOWER_GRP}…"
        gcloud compute instance-groups unmanaged remove-instances "${NEW_FOLLOWER_GRP}" \
            --zone="${FOLLOWER_ZONE}" --instances="${OLD_FOLLOWER_VM}" \
            --project="${PROJECT_ID}" 2>/dev/null || true
    fi

    ########################################
    # CLEANUP OLD INSTANCES (Issue 9: only after full success)
    ########################################

    if [[ -n "${OLD_LEADER_VM}" ]]; then
        log "Deleting OLD leader: ${OLD_LEADER_VM} (zone: ${OLD_LEADER_ZONE})"
        gcloud compute instances delete "${OLD_LEADER_VM}" --zone="${OLD_LEADER_ZONE}" --project="${PROJECT_ID}" --quiet
    fi

    if [[ -n "${OLD_FOLLOWER_VM}" ]]; then
        log "Deleting OLD follower: ${OLD_FOLLOWER_VM} (zone: ${OLD_FOLLOWER_ZONE})"
        gcloud compute instances delete "${OLD_FOLLOWER_VM}" --zone="${OLD_FOLLOWER_ZONE}" --project="${PROJECT_ID}" --quiet
    fi

    log "Deployment complete."
}

########################################
# MAIN
########################################

case "${1:-}" in
    build)
        check_prereqs
        build_images
        ;;
    tag)
        check_prereqs
        tag_images
        ;;
    deploy)
        check_prereqs
        deploy_instances
        ;;
    *)
        echo "Usage:"
        echo "  $0 build     # DEV: Build & push leader image only"
        echo "  $0 tag       # Tag images for TEST/PROD"
        echo "  $0 deploy    # Deploy leader only (DEV) or leader+follower (TEST/PROD)"
        exit 1
        ;;
esac
