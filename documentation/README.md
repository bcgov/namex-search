# SOLR Infrastructure Deployment Script

This repository contains Bash scripts to automate the deployment and management of a SOLR cluster in Google Cloud Platform (GCP) using a leader/follower architecture. The scripts are intentionally split into three responsibilities.

[Figma diagram](https://www.figma.com/board/nEjO2J7H63bFBgP2TKmjM0/Firebase-Infra?node-id=0-1&p=f&t=f41Xb5kWQbtkcSFH-0)

## Scripts Overview (Read This First)

| Script | Purpose | When to Run |
|------|--------|------------|
| `gcp-solr-infra.sh` | One-time infrastructure setup | New environment or major infra change / Infrastructure bootstrap |
| `update-solr-base-image.sh` | Create new VM templates with updated COS image | OS / security updates for base image (OS) |
| `deploy-solr.sh` | Deploy Solr + rotate VMs | App changes or after base image update |

> ⚠️ **Important:**
> Updating the base image does **not** affect running VMs until a redeploy is performed.

---

## Prerequisites

Before running the scripts, ensure the following:

- **Google Cloud SDK** installed and authenticated (`gcloud auth login`).
- **`oc` CLI** installed and authenticated (`oc login`) — required for importer job management.
- **Docker** installed — required for building images.
- **`make`** installed — required for building Solr images.
- `gcloud` is configured for your project.
- Proper IAM roles are assigned to your service accounts.
- Artifact Registry and required images exist.
- Startup script file exists at the specified path: `namex-solr/startupscript.txt`.
- **IAP tunneling** access is configured — VMs have no external IPs; the script uses `gcloud compute ssh --tunnel-through-iap` for health checks and replication configuration.

> The `deploy` command verifies both `gcloud` and `oc` authentication before proceeding.

---

# Usage

## Script 1: Infrastructure Bootstrap

Make sure to populate desired variable values correctly, e.g. ENV, LABEL, etc.

```
chmod +x gcp-solr-infra.sh
./documentation/gcp-solr-infra.sh
```
it is important to run this from 1 level higher as the script references location of startup.txt

⚠️ **Important Notes:**

- The script is fragile and may fail if resources are missing or already exist.
- You may need to set up the service account permissions for common project artifact registry manually.
- Zone-specific resource availability may block VM creation; you may need to wait for the resources to become available.

## Leader/Follower SOLR Replication

Replication is **configured automatically** by `deploy-solr.sh deploy` for test/prod environments. The script:

1. Creates the follower VM and waits for the Solr core to be ready.
2. SSHs into the follower via IAP tunnel and sets `solr.leaderUrl` pointing to the new leader's internal IP.

No manual steps are required.

To **verify** replication after deploy, SSH into the follower and run:

```bash
gcloud compute ssh <FOLLOWER_VM> --zone=<ZONE> --project=<PROJECT_ID> --tunnel-through-iap \
  --command="curl -s 'http://localhost:8983/solr/name_request_follower/config/requestHandler?componentName=/replication'"
```

You should see the leader URL in the response:
```json
{
  "responseHeader": {
    "status": 0,
    "QTime": 29
  },
  "config": {
    "requestHandler": {
      "/replication": {
        "name": "/replication",
        "class": "solr.ReplicationHandler",
        "follower": {
          "leaderUrl": "http://<LEADER_INTERNAL_IP>:8983/solr/name_request",
          "pollInterval": "00:00:30",
          "compression": "internal"
        }
      }
    }
  }
}
```

To verify data has replicated to the follower:
```bash
gcloud compute ssh <FOLLOWER_VM> --zone=<ZONE> --project=<PROJECT_ID> --tunnel-through-iap \
  --command="curl -s 'http://localhost:8983/solr/admin/cores?action=STATUS'"
```

You should see a non-empty follower core:
```json
{
  "responseHeader": {
    "status": 0,
    "QTime": 133
  },
  "initFailures": {},
  "status": {
    "name_request_follower": {
      "name": "name_request_follower",
      "instanceDir": "/var/solr/data/name_request_follower",
      "dataDir": "/var/solr/data/name_request_follower/data/",
      "config": "solrconfig.xml",
      "schema": "managed-schema.xml",
      "startTime": "2025-12-11T18:53:38.144Z",
      "uptime": 8710669,
      "index": {
        "numDocs": 8784132,
        "maxDoc": 8784134,
        "deletedDocs": 2,
        "segmentCount": 31,
        "current": true,
        "hasDeletions": true,
        "sizeInBytes": 3830194266,
        "size": "3.57 GB"
      }
    }
  }
}
```

## Script 2: Base Image (COS) Updates

You will need to update BOOT_DISK_IMAGE variable first.

```
chmod +x update-solr-base-image.sh
./documentation/update-solr-base-image.sh
```

Creates new instance templates. Uses an updated COS base image. Versions templates (e.g. -v2, -v3). Does not touch running VMs

## Script 3: Application Deploy & VM Rotation

Update relevant vars at the top of the script: `ENV`, `SOURCE_TAG`, `TEMPLATE_VERSION`.

```bash
chmod +x deploy-solr.sh
./documentation/deploy-solr.sh build   # DEV only: build & push images
./documentation/deploy-solr.sh tag     # Promote images from SOURCE_TAG → ENV
./documentation/deploy-solr.sh deploy  # Blue-green VM rotation
```

### What `deploy` does

1. **Verifies prerequisites** — checks `gcloud`, `docker`, `make`, `oc` are installed and authenticated.
2. **Creates a new leader VM** — tries each zone until one succeeds (zone failover).
3. **Waits for Solr** — polls the leader core via SSH/IAP tunnel until it responds to ping.
4. **Swaps leader backend** — adds new leader to instance group + backend service, waits for health check, then removes old leader from backend. Rolls back if the new leader fails health checks.
5. **Runs the importer job** — sets `REINDEX_CORE=True` in the OpenShift secret, creates a job from the importer CronJob, and waits up to 90 minutes for completion. Resets the flag on exit (via trap).
6. **(test/prod only) Creates a new follower VM** — same zone-failover approach.
7. **Configures replication** — SSHs into follower via IAP tunnel and sets `solr.leaderUrl` to the new leader's internal IP.
8. **Swaps follower backend** — same health-check-then-swap as leader.
9. **Cleans up old VMs** — deletes old leader and follower VMs only after full success.

### VM naming convention

VMs are named `namex-solr-{leader|follower}-{ENV}-{timestamp}`, e.g. `namex-solr-leader-prod-2026-03-30--220000`.

### Known limitations

- **Old VM detection** uses `--limit=1` — if multiple orphan VMs exist from failed runs, only the newest is detected and cleaned up. Manually check for orphans with:
  ```bash
  gcloud compute instances list --project a083gt-$ENV --sort-by=name
  ```
- **Subnet IP exhaustion** — the `/28` subnet has only 14 usable IPs. Orphan VMs from failed runs consume IPs. Clean up orphans before redeploying if you hit IP exhaustion errors.
