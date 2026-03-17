#!/usr/bin/env bash
set -euo pipefail

### ============================================================
###  SETUP VARIABLES
### ============================================================

PROJECT="a083gt"
ENV="dev"
LABEL="Dev"
PROJECT_ID="${PROJECT}-${ENV}"

VPC_NETWORK="bcr-vpc-${ENV}"
VPC_HOST_PROJECT="c4hnrd"
VPC_HOST_PROJECT_ID="${VPC_HOST_PROJECT}-${ENV}"

VPC_SUBNET="bcr-common-${ENV}-montreal"
REGION="northamerica-northeast1"
ZONE="northamerica-northeast1-a"
APP="namex"

TAGS="${APP}-solr"

ARTIFACT_REGISTRY_PROJECT="c4hnrd-tools"
BOOT_DISK_IMAGE="cos-121-18867-199-38"

### ============================================================
###  ENVIRONMENT-SPECIFIC CONFIG
### ============================================================

if [[ "$ENV" == "dev" ]]; then
  MACHINE_TYPE_FOLLOWER="custom-1-5120"
  BOOT_DISK_SIZE_FOLLOWER="10GiB"
  FOLLOWER_JVM_MEM="1g"

  MACHINE_TYPE_LEADER="custom-1-5120"
  BOOT_DISK_SIZE_LEADER="10GiB"
  LEADER_JVM_MEM="1g"
elif [[ "$ENV" == "test" ]]; then
  MACHINE_TYPE_FOLLOWER="custom-1-5120"
  BOOT_DISK_SIZE_FOLLOWER="10GiB"
  FOLLOWER_JVM_MEM="1g"

  MACHINE_TYPE_LEADER="custom-1-5120"
  BOOT_DISK_SIZE_LEADER="10GiB"
  LEADER_JVM_MEM="1g"
elif [[ "$ENV" == "sandbox" ]]; then
  MACHINE_TYPE_FOLLOWER="custom-1-6656"
  BOOT_DISK_SIZE_FOLLOWER="16GiB"
  FOLLOWER_JVM_MEM="1g"

  MACHINE_TYPE_LEADER="custom-1-6656"
  BOOT_DISK_SIZE_LEADER="24GiB"
  LEADER_JVM_MEM="1g"
elif [[ "$ENV" == "prod" ]]; then
  MACHINE_TYPE_FOLLOWER="custom-1-8192-ext"
  BOOT_DISK_SIZE_FOLLOWER="16GiB"
  FOLLOWER_JVM_MEM="1g"

  MACHINE_TYPE_LEADER="custom-2-10240"
  BOOT_DISK_SIZE_LEADER="24GiB"
  LEADER_JVM_MEM="2g"
fi

FOLLOWER_ROLE="follower"
LEADER_ROLE="leader"

FOLLOWER_GRP_NAME="${APP}-solr-${FOLLOWER_ROLE}-grp-${ENV}"
LEADER_GRP_NAME="${APP}-solr-${LEADER_ROLE}-grp-${ENV}"
INSTANCE_TEMPLATE_FOLLOWER="${APP}-solr-${FOLLOWER_ROLE}-vm-tmpl-${ENV}"
INSTANCE_TEMPLATE_LEADER="${APP}-solr-${LEADER_ROLE}-vm-tmpl-${ENV}"

FOLLOWER_IMAGE="name-request-solr-${FOLLOWER_ROLE}"
LEADER_IMAGE="name-request-solr-${LEADER_ROLE}"

IMAGE_PROJECT="${VPC_HOST_PROJECT}-tools"
IMAGE_REPO="vm-repo"

HEALTH_CHECK_NAME="${APP}-solr-health-check-$ENV"

SERVICE_ACCOUNT=$(gcloud iam service-accounts list \
  --format="value(email)" \
  --filter 'displayName="Default compute service account"' \
  --project="$PROJECT_ID")

## ============================================================
##  ARTIFACT REGISTRY PERMISSIONS
## ============================================================

echo "➤ Adding artifact registry pull permissions..."

gcloud projects add-iam-policy-binding "$ARTIFACT_REGISTRY_PROJECT" \
  --member "serviceAccount:${SERVICE_ACCOUNT}" \
  --role "roles/artifactregistry.serviceAgent"

### ============================================================
###  ASSIGN INTERNAL STATIC IPs
### ============================================================

echo "➤ Creating internal load balancer IPs..."

LB_LEADER_IP=$(gcloud compute addresses create ${APP}-solr-ilb-ip \
  --region="$REGION" \
  --subnet="projects/$VPC_HOST_PROJECT_ID/regions/$REGION/subnetworks/$VPC_SUBNET" \
  --project="$PROJECT_ID" \
  --format="value(address)")

if [[ "$ENV" != "dev" ]]; then
  LB_FOLLOWER_IP=$(gcloud compute addresses create ${APP}-solr-follower-ilb-ip \
    --region="$REGION" \
    --subnet="projects/$VPC_HOST_PROJECT_ID/regions/$REGION/subnetworks/$VPC_SUBNET" \
    --project="$PROJECT_ID" \
    --format="value(address)")
fi

echo "  Leader ILB IP:   $LB_LEADER_IP"
[[ "$ENV" != "dev" ]] && echo "  Follower ILB IP: $LB_FOLLOWER_IP"

### ============================================================
###  SOLR INSTANCE GROUPS
### ============================================================

# Helper: back up an existing instance group to <name>-old, then delete the original
backup_instance_group() {
  local GRP_NAME="$1"
  if gcloud compute instance-groups unmanaged describe "$GRP_NAME" \
       --zone="$ZONE" --project="$PROJECT_ID" &>/dev/null; then
    echo "  ⚠ Instance group '$GRP_NAME' already exists — backing up to '${GRP_NAME}-old'..."
    gcloud compute instance-groups unmanaged create "${GRP_NAME}-old" \
      --project="$PROJECT_ID" --zone="$ZONE" 2>/dev/null || true
    OLD_INSTANCES=$(gcloud compute instance-groups unmanaged list-instances "$GRP_NAME" \
      --zone="$ZONE" --project="$PROJECT_ID" --format="value(instance)" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
    if [[ -n "$OLD_INSTANCES" ]]; then
      gcloud compute instance-groups unmanaged add-instances "${GRP_NAME}-old" \
        --zone="$ZONE" --instances="$OLD_INSTANCES" --project="$PROJECT_ID"
      gcloud compute instance-groups unmanaged remove-instances "$GRP_NAME" \
        --zone="$ZONE" --instances="$OLD_INSTANCES" --project="$PROJECT_ID"
    fi
    gcloud compute instance-groups unmanaged delete "$GRP_NAME" \
      --project="$PROJECT_ID" --zone="$ZONE" --quiet
  fi
}

echo "➤ Creating instance groups..."

backup_instance_group "$LEADER_GRP_NAME"

gcloud compute instance-groups unmanaged create "$LEADER_GRP_NAME" \
  --project="$PROJECT_ID" --zone="$ZONE"

gcloud compute instance-groups unmanaged set-named-ports "$LEADER_GRP_NAME" \
  --project="$PROJECT_ID" --zone="$ZONE" \
  --named-ports=http:8983

if [[ "$ENV" != "dev" ]]; then
  backup_instance_group "$FOLLOWER_GRP_NAME"

  gcloud compute instance-groups unmanaged create "$FOLLOWER_GRP_NAME" \
    --project="$PROJECT_ID" --zone="$ZONE"

  gcloud compute instance-groups unmanaged set-named-ports "$FOLLOWER_GRP_NAME" \
    --project="$PROJECT_ID" --zone="$ZONE" \
    --named-ports=http:8983
fi

### ============================================================
###  HEALTH CHECK
### ============================================================

echo "➤ Creating health check..."

gcloud compute health-checks create tcp "$HEALTH_CHECK_NAME" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --port=8983 \
  --check-interval=5s \
  --timeout=5s \
  --unhealthy-threshold=2 \
  --healthy-threshold=2 || true

### ============================================================
###  FIREWALL RULES
### ============================================================

echo "➤ Creating firewall rules for ILB and health checks..."

gcloud compute firewall-rules create allow-${APP}-solr-ilb-hc \
  --priority=1000 \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:8983 \
  --source-ranges=35.191.0.0/16,130.211.0.0/22 \
  --network="$VPC_NETWORK" \
  --target-tags="$TAGS" \
  --project="$VPC_HOST_PROJECT_ID" || true

LB_SUBNET_CIDR=$(gcloud compute networks subnets describe "$VPC_SUBNET" \
  --region="$REGION" \
  --project="$VPC_HOST_PROJECT_ID" \
  --format="value(ipCidrRange)")

gcloud compute firewall-rules create allow-${APP}-solr-ilb \
  --priority=1000 \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:8983 \
  --source-ranges="$LB_SUBNET_CIDR" \
  --network="$VPC_NETWORK" \
  --target-tags="$TAGS" \
  --project="$VPC_HOST_PROJECT_ID" || true

### ============================================================
###  SOLR INSTANCE TEMPLATES (with metadata including ZONE)
### ============================================================

DEVICE_NAME="${APP}-solr-disk-$ENV"
PATH_TO_STARTUP_SCRIPT="../${APP}-solr/startupscript.txt"

# Helper: back up an existing instance template to <name>-old, then delete the original
backup_instance_template() {
  local TMPL_NAME="$1"
  if gcloud compute instance-templates describe "$TMPL_NAME" \
       --project="$PROJECT_ID" &>/dev/null; then
    echo "  ⚠ Instance template '$TMPL_NAME' already exists — backing up to '${TMPL_NAME}-old'..."
    gcloud compute instance-templates create "${TMPL_NAME}-old" \
      --source-instance-template="$TMPL_NAME" \
      --project="$PROJECT_ID" 2>/dev/null || true
    gcloud compute instance-templates delete "$TMPL_NAME" \
      --project="$PROJECT_ID" --quiet
  fi
}

echo "➤ Creating SOLR instance templates..."

backup_instance_template "$INSTANCE_TEMPLATE_LEADER"

gcloud compute instance-templates create "$INSTANCE_TEMPLATE_LEADER" \
  --project="$PROJECT_ID" \
  --machine-type="$MACHINE_TYPE_LEADER" \
  --network-interface=network=projects/$VPC_HOST_PROJECT_ID/global/networks/$VPC_NETWORK,subnet=projects/$VPC_HOST_PROJECT_ID/regions/$REGION/subnetworks/$VPC_SUBNET,stack-type=IPV4_ONLY,no-address \
  --metadata-from-file=startup-script="$PATH_TO_STARTUP_SCRIPT" \
  --metadata=google-logging-enabled=true,role=$LEADER_ROLE,env=$ENV,label=$LABEL,jvm_mem=$LEADER_JVM_MEM,image=$LEADER_IMAGE,image_project=$IMAGE_PROJECT,image_repo=$IMAGE_REPO,zone=$ZONE \
  --maintenance-policy=MIGRATE \
  --provisioning-model=STANDARD \
  --service-account="$SERVICE_ACCOUNT" \
  --scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/trace.append \
  --tags="$TAGS" \
  --create-disk=auto-delete=yes,boot=yes,device-name="$DEVICE_NAME",image=projects/cos-cloud/global/images/$BOOT_DISK_IMAGE,mode=rw,size="$BOOT_DISK_SIZE_LEADER",type=pd-ssd \
  --no-shielded-secure-boot \
  --shielded-vtpm \
  --shielded-integrity-monitoring

if [[ "$ENV" != "dev" ]]; then
  backup_instance_template "$INSTANCE_TEMPLATE_FOLLOWER"

  gcloud compute instance-templates create "$INSTANCE_TEMPLATE_FOLLOWER" \
    --project="$PROJECT_ID" \
    --machine-type="$MACHINE_TYPE_FOLLOWER" \
    --network-interface=network=projects/$VPC_HOST_PROJECT_ID/global/networks/$VPC_NETWORK,subnet=projects/$VPC_HOST_PROJECT_ID/regions/$REGION/subnetworks/$VPC_SUBNET,stack-type=IPV4_ONLY,no-address \
    --metadata-from-file=startup-script="$PATH_TO_STARTUP_SCRIPT" \
    --metadata=google-logging-enabled=true,role=$FOLLOWER_ROLE,env=$ENV,label=$LABEL,jvm_mem=$FOLLOWER_JVM_MEM,image=$FOLLOWER_IMAGE,image_project=$IMAGE_PROJECT,image_repo=$IMAGE_REPO,zone=$ZONE \
    --maintenance-policy=MIGRATE \
    --provisioning-model=STANDARD \
    --service-account="$SERVICE_ACCOUNT" \
    --scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/trace.append \
    --tags="$TAGS" \
    --create-disk=auto-delete=yes,boot=yes,device-name="$DEVICE_NAME",image=projects/cos-cloud/global/images/$BOOT_DISK_IMAGE,mode=rw,size="$BOOT_DISK_SIZE_FOLLOWER",type=pd-ssd \
    --no-shielded-secure-boot \
    --shielded-vtpm \
    --shielded-integrity-monitoring
fi

### ============================================================
###  LOAD BALANCERS (Leader & Follower)
### ============================================================

echo "➤ Creating backend services and ILB forwarding rules..."

gcloud compute backend-services create ${APP}-solr-leader-backend \
  --protocol=TCP \
  --health-checks="$HEALTH_CHECK_NAME" \
  --health-checks-region="$REGION" \
  --region="$REGION" \
  --load-balancing-scheme=INTERNAL \
  --project="$PROJECT_ID" || true

gcloud compute backend-services add-backend ${APP}-solr-leader-backend \
  --instance-group="$LEADER_GRP_NAME" \
  --instance-group-zone="$ZONE" \
  --region="$REGION" \
  --project="$PROJECT_ID" || true

if [[ "$ENV" != "dev" ]]; then
  gcloud compute backend-services create ${APP}-solr-follower-backend \
    --protocol=TCP \
    --health-checks="$HEALTH_CHECK_NAME" \
    --health-checks-region="$REGION" \
    --region="$REGION" \
    --load-balancing-scheme=INTERNAL \
    --project="$PROJECT_ID"

  gcloud compute backend-services add-backend ${APP}-solr-follower-backend \
    --instance-group="$FOLLOWER_GRP_NAME" \
    --instance-group-zone="$ZONE" \
    --region="$REGION" \
    --project="$PROJECT_ID" || true
fi

gcloud compute forwarding-rules create ${APP}-solr-tcp-ilb-rule \
  --load-balancing-scheme=INTERNAL \
  --network="projects/$VPC_HOST_PROJECT_ID/global/networks/$VPC_NETWORK" \
  --subnet="projects/$VPC_HOST_PROJECT_ID/regions/$REGION/subnetworks/$VPC_SUBNET" \
  --address="$LB_LEADER_IP" \
  --ports=8983 \
  --backend-service=${APP}-solr-leader-backend \
  --backend-service-region="$REGION" \
  --region="$REGION" \
  --project="$PROJECT_ID" || true

if [[ "$ENV" != "dev" ]]; then
  gcloud compute forwarding-rules create ${APP}-solr-follower-ilb-rule \
    --load-balancing-scheme=INTERNAL \
    --network="projects/$VPC_HOST_PROJECT_ID/global/networks/$VPC_NETWORK" \
    --subnet="projects/$VPC_HOST_PROJECT_ID/regions/$REGION/subnetworks/$VPC_SUBNET" \
    --address="$LB_FOLLOWER_IP" \
    --ports=8983 \
    --backend-service=${APP}-solr-follower-backend \
    --backend-service-region="$REGION" \
    --region="$REGION" \
    --project="$PROJECT_ID"
fi

### ============================================================
###  CREATE VMs
### ============================================================

NEW_LEADER_VM="${APP}-solr-${LEADER_ROLE}-$(date -u +"%Y-%m-%d--%H%M%S")"

echo "➤ Creating leader VM: $NEW_LEADER_VM from template $INSTANCE_TEMPLATE_LEADER"
gcloud compute instances create "$NEW_LEADER_VM" \
  --source-instance-template "$INSTANCE_TEMPLATE_LEADER" \
  --zone "$ZONE" \
  --project "$PROJECT_ID"

gcloud compute instance-groups unmanaged add-instances "$LEADER_GRP_NAME" \
  --zone="$ZONE" \
  --instances="$NEW_LEADER_VM" \
  --project="$PROJECT_ID"

if [[ "$ENV" != "dev" ]]; then
  NEW_FOLLOWER_VM="${APP}-solr-${FOLLOWER_ROLE}-$(date -u +"%Y-%m-%d--%H%M%S")"

  echo "➤ Creating follower VM: $NEW_FOLLOWER_VM from template $INSTANCE_TEMPLATE_FOLLOWER"
  gcloud compute instances create "$NEW_FOLLOWER_VM" \
    --source-instance-template "$INSTANCE_TEMPLATE_FOLLOWER" \
    --zone "$ZONE" \
    --project "$PROJECT_ID"

  gcloud compute instance-groups unmanaged add-instances "$FOLLOWER_GRP_NAME" \
    --zone="$ZONE" \
    --instances="$NEW_FOLLOWER_VM" \
    --project="$PROJECT_ID"
fi

echo "✔ SOLR infrastructure creation complete!"
echo "Next step: Deploy & create Solr VMs."
