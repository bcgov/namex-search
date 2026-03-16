#!/usr/bin/env bash
set -euo pipefail

########################################
# CONFIGURATION
########################################

ENV="test"   # dev / test / prod
SOURCE_TAG="dev"

PROJECT="<YOUR_PROJECT>"
PROJECT_ID="${PROJECT}-${ENV}"
ARTIFACT_REGISTRY_PROJECT="c4hnrd-tools"

ZONE="northamerica-northeast1-a"
REGION="northamerica-northeast1"
REPO_PATH="${REGION}-docker.pkg.dev/${ARTIFACT_REGISTRY_PROJECT}/vm-repo"

# Template version must match what update-solr-base-image.sh created
TEMPLATE_VERSION="v1"

LEADER_TEMPLATE="namex-solr-leader-vm-tmpl-${ENV}-${TEMPLATE_VERSION}"
FOLLOWER_TEMPLATE="namex-solr-follower-vm-tmpl-${ENV}-${TEMPLATE_VERSION}"


########################################
# HELPER FUNCTIONS
########################################

log() { echo -e "\n🔹  $1\n"; }

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

    log "Determining old leader & follower VMs…"
    OLD_LEADER_VM=$(gcloud compute instances list --format="value(name)" \
        --filter "name:namex-solr-leader-${ENV}" --project="${PROJECT_ID}" || true)

    OLD_FOLLOWER_VM=$(gcloud compute instances list --format="value(name)" \
        --filter "name:namex-solr-follower-${ENV}" --project="${PROJECT_ID}" || true)

    log "OLD_LEADER_VM=${OLD_LEADER_VM}"
    log "OLD_FOLLOWER_VM=${OLD_FOLLOWER_VM}"

    #####################################
    # CREATE NEW LEADER
    #####################################
    log "Creating NEW Leader VM: ${NEW_LEADER_VM}"

    gcloud compute instances create "${NEW_LEADER_VM}" \
      --source-instance-template "${LEADER_TEMPLATE}" \
      --zone "${ZONE}" \
      --project "${PROJECT_ID}"

    log "Adding NEW leader to unmanaged group…"
    gcloud compute instance-groups unmanaged add-instances \
        "namex-solr-leader-grp-${ENV}" \
        --zone "${ZONE}" \
        --instances "${NEW_LEADER_VM}" \
        --project "${PROJECT_ID}"

    log "⚠️  MANUAL STEP REQUIRED"
    log "Run the importer on the NEW leader VM:"
    log "  ${NEW_LEADER_VM}"
    log ""
    read -rp "Press ENTER to continue after importer has completed..."

    ########################################
    # DEV ENV → LEADER ONLY
    ########################################
    if [[ "${ENV}" == "dev" ]]; then
        log "DEV environment: follower instance not required. Skipping follower creation."

        if [[ -n "${OLD_LEADER_VM}" ]]; then
            log "Deleting OLD leader: ${OLD_LEADER_VM}"
            gcloud compute instances delete "${OLD_LEADER_VM}" --zone="${ZONE}" --project="${PROJECT_ID}" --quiet
        fi

        log "Deployment complete (DEV: leader only)."
        return
    fi

    ########################################
    # TEST/PROD DEPLOY → CREATE FOLLOWER
    ########################################

    log "Creating NEW Follower VM: ${NEW_FOLLOWER_VM}"
    gcloud compute instances create "${NEW_FOLLOWER_VM}" \
      --source-instance-template "${FOLLOWER_TEMPLATE}" \
      --zone "${ZONE}" \
      --project "${PROJECT_ID}"

    log "Fetching internal IPs…"

    NEW_LEADER_INTERNAL_IP=$(gcloud compute instances describe "${NEW_LEADER_VM}" \
        --zone "${ZONE}" --project "${PROJECT_ID}" \
        --format='value(networkInterfaces[0].networkIP)')

    NEW_FOLLOWER_INTERNAL_IP=$(gcloud compute instances describe "${NEW_FOLLOWER_VM}" \
        --zone "${ZONE}" --project "${PROJECT_ID}" \
        --format='value(networkInterfaces[0].networkIP)')

    log "Setting follower replication properties…"

    curl -X POST -H 'Content-type: application/json' \
      -d "{\"set-user-property\":{\"solr.leaderUrl\": \"http://${NEW_LEADER_INTERNAL_IP}:8983/solr/name_request\"}}" \
      "http://${NEW_FOLLOWER_INTERNAL_IP}:8983/solr/name_request_follower/config/requestHandler?componentName=/replication"

    curl -X POST -H 'Content-type: application/json' \
      -d "{\"set-user-property\":{\"solr.leaderUrl\": \"http://${NEW_LEADER_INTERNAL_IP}:8983/solr/name_request\"}}" \
      "http://${NEW_FOLLOWER_INTERNAL_IP}:8983/solr/name_request_follower/config/requestHandler"

    log "Adding follower to unmanaged group…"
    gcloud compute instance-groups unmanaged add-instances \
        "namex-solr-follower-grp-${ENV}" \
        --zone "${ZONE}" \
        --instances "${NEW_FOLLOWER_VM}" \
        --project "${PROJECT_ID}"

    ########################################
    # DELETE OLD INSTANCES
    ########################################

    if [[ -n "${OLD_LEADER_VM}" ]]; then
        log "Deleting OLD leader: ${OLD_LEADER_VM}"
        gcloud compute instances delete "${OLD_LEADER_VM}" --zone="${ZONE}" --project="${PROJECT_ID}" --quiet
    fi

    if [[ -n "${OLD_FOLLOWER_VM}" ]]; then
        log "Deleting OLD follower: ${OLD_FOLLOWER_VM}"
        gcloud compute instances delete "${OLD_FOLLOWER_VM}" --zone="${ZONE}" --project="${PROJECT_ID}" --quiet
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
