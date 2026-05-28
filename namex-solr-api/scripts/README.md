# namex-solr-api Cloud Scheduler Setup

## Overview

This directory contains the one-time setup script for creating **GCP Cloud Scheduler jobs** that periodically trigger the `namex-solr-api` sync and heartbeat endpoints.

These schedulers are part of the **post-migration infrastructure** for the namex Solr modernization — replacing the legacy Oracle-backed Solr with a new GCP-hosted Solr service (`namex-solr-api`). The schedulers ensure the new Solr index stays current and healthy after the migration is complete.

> **No CD pipeline is required.** This script is run once per environment by someone with GCP admin access. The schedulers persist in GCP Cloud Scheduler and continue running indefinitely after creation.

---

## Background

### Why these schedulers exist

The `namex-solr-api` service maintains a Solr index used for name conflict searching during Name Request (NR) processing. The index has two ongoing operational needs:

| Need | Problem without it | Solved by |
|---|---|---|
| **Sync** | Pending DB update events accumulate but never get pushed to Solr — index goes stale | Sync scheduler |
| **Heartbeat** | Solr follower may fall behind the leader silently — stale data served to users with no alert | Heartbeat scheduler |

### Architecture

```
GCP Cloud Scheduler
  │
  ├── namex-solr-sync-{env}          (every 5 min, business hours)
  │     └── GET /internal/solr/update/sync
  │           └── Reads pending SolrDocEvent records from namex_solr DB
  │               → pushes batched updates to Solr leader
  │
  └── namex-solr-heartbeat-{env}     (every 30 min, business hours)
        └── GET /internal/solr/update/sync/heartbeat
              └── Checks Solr follower replication lag
                  → returns 500 if follower is behind threshold
```

### Endpoint definitions

Both endpoints are defined in:
```
namex-solr-api/src/namex_solr_api/resources/internal/solr/update/sync.py
```

| Endpoint | Method | Auth | What it does |
|---|---|---|---|
| `/internal/solr/update/sync` | GET | None (internal) | Processes `PENDING`/`ERROR` SolrDocEvents and pushes docs to Solr |
| `/internal/solr/update/sync/heartbeat` | GET | None (internal) | Validates Solr follower replication is within threshold; returns 500 on failure |

### Relationship to other jobs

These schedulers call `namex-solr-api` — they do **not** replace or interact with:

| Existing job | What it does | Layer |
|---|---|---|
| `namex-solr-importer` (OCP CronJob, 9:45 AM daily) | Full re-index from LEAR + COLIN + NameX DBs | Data pipeline |
| GCP VM health check (`namex-solr-health-check-{env}`) | TCP port 8983 check | Infrastructure |
| Solr ping (`gcp-solr-infra.sh`) | HTTP `/solr/name_request/admin/ping` | Infrastructure |
| Healthcheck sidecar | Container liveness + basic search | Application |
| **Sync scheduler (ours)** | Pushes pending DB events → Solr | **Data freshness** |
| **Heartbeat scheduler (ours)** | Verifies follower replication accuracy | **Data integrity** |

The importer handles the daily full re-index. Our schedulers handle real-time incremental updates between importer runs.

---

## Environments

| Environment | GCP Project | Service Account | DEV URL |
|---|---|---|---|
| dev | `a083gt-dev` | `sa-job@a083gt-dev.iam.gserviceaccount.com` | `https://namex-solr-api-dev-475224072965.northamerica-northeast1.run.app` |
| test | `a083gt-test` | `sa-job@a083gt-test.iam.gserviceaccount.com` | `https://namex-solr-api-test-457237769279.northamerica-northeast1.run.app` |
| sandbox | `a083gt-integration` | `sa-job@a083gt-integration.iam.gserviceaccount.com` | Confirm project number before running |
| prod | `a083gt-prod` | `sa-job@a083gt-prod.iam.gserviceaccount.com` | Confirm project number before running |

> **Service account pattern:** `sa-job` is the org standard for jobs that make outbound HTTP calls only (no Cloud SQL access). Do not use `sa-api` — that is for the API service itself and legacy jobs that need direct DB access.

---

## Scheduler configuration

| Job | Schedule | Frequency | Timezone |
|---|---|---|---|
| `namex-solr-sync-{env}` | `*/5 9-17 * * MON-FRI` | Every 5 min, business hours | America/Vancouver |
| `namex-solr-heartbeat-{env}` | `0,30 9-17 * * MON-FRI` | Every 30 min, business hours | America/Vancouver |

Business hours only — aligns with org standard (see `search-solr-sync-job-dev` and `search-solr-sync-heartbeat-job-dev` in `k973yf-dev` as reference).

---

## Prerequisites

### 1. gcloud CLI

Install from: https://cloud.google.com/sdk/docs/install

Authenticate:
```bash
gcloud auth login
gcloud config set project a083gt-dev   # or target environment
```

Or use **GCP Cloud Shell** — gcloud is pre-installed and pre-authenticated.

### 2. GCP admin access

You need sufficient IAM permissions in the target project to:
- Create Cloud Scheduler jobs (`cloudscheduler.jobs.create`)
- List Cloud Scheduler jobs (`cloudscheduler.jobs.list`)

### 3. Service account invoker permission

The `sa-job@a083gt-{env}.iam.gserviceaccount.com` service account must have `roles/run.invoker` on the `namex-solr-api` Cloud Run service in each environment.

Verify with:
```bash
gcloud run services get-iam-policy namex-solr-api \
  --region=northamerica-northeast1 \
  --project=a083gt-dev
```

If missing, ask SRE to grant:
```bash
gcloud run services add-iam-policy-binding namex-solr-api \
  --region=northamerica-northeast1 \
  --project=a083gt-{env} \
  --member="serviceAccount:sa-job@a083gt-{env}.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

---

## Running the script

### Step 1 — Confirm namex-solr-api is deployed in the target environment

The scheduler calls `namex-solr-api` — the service must be running first.

Verify:
```bash
gcloud run services describe namex-solr-api \
  --region=northamerica-northeast1 \
  --project=a083gt-dev
```

### Step 2 — Run the script

```bash
chmod +x create-scheduler.sh
./create-scheduler.sh dev
```

The script accepts one argument: `dev | test | sandbox | prod`

```
./create-scheduler.sh dev      ← creates jobs in a083gt-dev
./create-scheduler.sh test     ← creates jobs in a083gt-test
./create-scheduler.sh sandbox  ← requires PROJECT_NUMBER to be filled in first
./create-scheduler.sh prod     ← requires PROJECT_NUMBER to be filled in first
```

> **For sandbox and prod:** Update `PROJECT_NUMBER` in the script for those environments before running. The project number can be found in the GCP Console → Project Settings or via `gcloud projects describe a083gt-{env} --format='value(projectNumber)'`.

### Step 3 — Verify jobs were created

The script lists matching jobs automatically. You can also verify manually:

```bash
gcloud scheduler jobs list \
  --project=a083gt-dev \
  --location=northamerica-northeast1 \
  --filter="name:namex-solr"
```

### Step 4 — Force a test run

Trigger each job once manually to confirm HTTP 200:

```bash
gcloud scheduler jobs run namex-solr-sync-dev \
  --project=a083gt-dev \
  --location=northamerica-northeast1

gcloud scheduler jobs run namex-solr-heartbeat-dev \
  --project=a083gt-dev \
  --location=northamerica-northeast1
```

Check execution status in GCP Console → Cloud Scheduler → click job → **View logs**.

Expected: `Sync successful.` / `Follower synchronization is healthy.`

---

## Deployment order

Deploy environments in this order — do not proceed to the next until the previous is validated:

```
dev → test → sandbox → prod
```

`sandbox` and `prod` should only be set up once `namex-solr-api` is confirmed deployed and stable in those environments.

---

## Updating or deleting jobs

### Update schedule or config

```bash
gcloud scheduler jobs update http namex-solr-sync-dev \
  --project=a083gt-dev \
  --location=northamerica-northeast1 \
  --schedule="*/5 9-17 * * MON-FRI"
```

### Pause a job

```bash
gcloud scheduler jobs pause namex-solr-sync-dev \
  --project=a083gt-dev \
  --location=northamerica-northeast1
```

### Delete a job

```bash
gcloud scheduler jobs delete namex-solr-sync-dev \
  --project=a083gt-dev \
  --location=northamerica-northeast1
```

### Re-run the script (jobs already exist)

If jobs already exist and you need to recreate them, delete first then re-run:
```bash
gcloud scheduler jobs delete namex-solr-sync-dev --project=a083gt-dev --location=northamerica-northeast1
gcloud scheduler jobs delete namex-solr-heartbeat-dev --project=a083gt-dev --location=northamerica-northeast1
./create-scheduler.sh dev
```

---

## Troubleshooting

### Scheduler fires but returns non-200

Check Cloud Run logs:
```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=namex-solr-api' \
  --project=a083gt-dev \
  --limit=50 \
  --format="table(timestamp, textPayload)"
```

Common causes:
| Symptom | Likely cause |
|---|---|
| HTTP 401/403 | `sa-job` missing `run.invoker` on the Cloud Run service |
| HTTP 500 from heartbeat | Solr follower replication lag exceeds threshold — check Solr follower status |
| HTTP 500 from sync | DB connectivity issue or malformed SolrDocEvent in queue |
| Job never fires | Cloud Scheduler service account missing `run.invoker` — check project IAM |

### Heartbeat returns 500

The heartbeat endpoint checks two things:
1. Solr follower polling is enabled (not disabled)
2. Last replication timestamp is within `LAST_REPLICATION_THRESHOLD` hours

If the follower falls behind, check:
- Solr follower replication status at `http://{follower-host}:8983/solr/name_request/replication?command=details`
- Network connectivity between leader and follower VMs

### Coverage CI failure on `namex-solr-api`

The `namex-solr-api` test suite currently has a pre-existing coverage gap (placeholder tests only — `test_placeholder.py`). This is a known issue tracked separately and is unrelated to these scheduler scripts which add no Python code.

---

## Related resources

| Resource | Location |
|---|---|
| Sync + heartbeat endpoint code | `namex-solr-api/src/namex_solr_api/resources/internal/solr/update/sync.py` |
| GCP Solr infra setup script | `documentation/gcp-solr-infra.sh` |
| Solr deploy script | `documentation/deploy-solr.sh` |
| Reference schedulers (business search) | GCP Console → `k973yf-dev` → Cloud Scheduler → `search-solr-*` |
| namex-solr-importer (daily full re-index) | `namex-solr-importer/` |
