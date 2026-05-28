#!/usr/bin/env bash
set -euo pipefail

### ============================================================
###  namex-solr-api Cloud Scheduler Jobs
###
###  Creates Cloud Scheduler HTTP jobs to periodically trigger
###  the namex-solr-api sync and heartbeat endpoints.
###
###  Prerequisites:
###    - gcloud CLI installed and authenticated
###    - GCP admin access to the target project
###    - sa-job@a083gt-{env}.iam.gserviceaccount.com must have
###      roles/run.invoker on the namex-solr-api Cloud Run service
###
###  Usage:
###    ./create-scheduler.sh [dev|test|sandbox|prod]
###
###  Example:
###    ./create-scheduler.sh dev
### ============================================================

ENV="${1:-dev}"

case "${ENV}" in
  dev)
    PROJECT="a083gt-dev"
    PROJECT_NUMBER="475224072965"
    ;;
  test)
    PROJECT="a083gt-test"
    PROJECT_NUMBER="457237769279"
    ;;
  sandbox)
    PROJECT="a083gt-integration"
    PROJECT_NUMBER=""
    echo "ERROR: sandbox PROJECT_NUMBER not yet confirmed. Update this script before running."
    exit 1
    ;;
  prod)
    PROJECT="a083gt-prod"
    PROJECT_NUMBER=""
    echo "ERROR: prod PROJECT_NUMBER not yet confirmed. Update this script before running."
    exit 1
    ;;
  *)
    echo "ERROR: Unknown environment '${ENV}'. Use: dev | test | sandbox | prod"
    exit 1
    ;;
esac

REGION="northamerica-northeast1"
SERVICE_ACCOUNT="sa-job@${PROJECT}.iam.gserviceaccount.com"
SERVICE_URL="https://namex-solr-api-${ENV}-${PROJECT_NUMBER}.northamerica-northeast1.run.app"

echo ""
echo "========================================"
echo " Creating namex-solr-api schedulers"
echo " ENV:     ${ENV}"
echo " PROJECT: ${PROJECT}"
echo " URL:     ${SERVICE_URL}"
echo "========================================"
echo ""

# ----------------------------------------------------------
# Job 1: Sync
# Pushes pending DB events to the Solr index.
# Runs every 5 minutes during business hours Mon-Fri.
# ----------------------------------------------------------
echo "Creating namex-solr-sync-${ENV}..."

gcloud scheduler jobs create http "namex-solr-sync-${ENV}" \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --schedule="*/5 9-17 * * MON-FRI" \
  --time-zone="America/Vancouver" \
  --uri="${SERVICE_URL}/internal/solr/update/sync" \
  --http-method=GET \
  --oidc-service-account-email="${SERVICE_ACCOUNT}" \
  --oidc-token-audience="${SERVICE_URL}" \
  --description="Syncs pending DB events to namex Solr index"

echo "✓ namex-solr-sync-${ENV} created"
echo ""

# ----------------------------------------------------------
# Job 2: Heartbeat
# Verifies Solr follower replication is healthy.
# Runs every 30 minutes during business hours Mon-Fri.
# ----------------------------------------------------------
echo "Creating namex-solr-heartbeat-${ENV}..."

gcloud scheduler jobs create http "namex-solr-heartbeat-${ENV}" \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --schedule="0,30 9-17 * * MON-FRI" \
  --time-zone="America/Vancouver" \
  --uri="${SERVICE_URL}/internal/solr/update/sync/heartbeat" \
  --http-method=GET \
  --oidc-service-account-email="${SERVICE_ACCOUNT}" \
  --oidc-token-audience="${SERVICE_URL}" \
  --description="Verifies namex Solr follower replication health"

echo "✓ namex-solr-heartbeat-${ENV} created"
echo ""

# ----------------------------------------------------------
# Verify
# ----------------------------------------------------------
echo "Verifying created jobs..."
gcloud scheduler jobs list \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --filter="name:namex-solr"

echo ""
echo "Done. Both scheduler jobs created for ENV=${ENV}."
