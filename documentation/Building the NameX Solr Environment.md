*NOTE: this expects you to already have your gcloud setup / permissions to the gcp environment*

SETUP
```
PROJECT=
```

```
ENV=
```

```
PROJECT_ID=$PROJECT-$ENV
```

### API Networking Stuff
1. Reserve an external static IP for the api
   ```
   STATIC_IP_NAME=namex-solr-api-ip-$ENV
   ```
   
   ```
   gcloud compute addresses create $STATIC_IP_NAME --project=$PROJECT_ID --region=northamerica-northeast1
   ```
   *Set the generated static IP (used later)*
   ```
   STATIC_IP=$(gcloud compute addresses list --filter NAME:$STATIC_IP_NAME --project=$PROJECT_ID --format="value(address_range())")
   ```
2. Create the router
   ```
   ROUTER_NAME=namex-solr-api-router-$ENV
   ```
   
   ```
   gcloud compute routers create $ROUTER_NAME --project=$PROJECT_ID --region=northamerica-northeast1 --network=default
   ```
3. Create the NAT gateway
   ```
   NAT_GW_NAME=namex-solr-api-nat-gw-$ENV
   ```
   
   ```
   gcloud compute routers nats create $NAT_GW_NAME --router=$ROUTER_NAME --region=northamerica-northeast1 --nat-all-subnet-ip-ranges --nat-external-ip-pool=$STATIC_IP_NAME --project=$PROJECT_ID
   ```
4. Create the vm connector
   ```
   CONNECTOR_NAME=namex-solr-connector-$ENV
   ```
   
   ```
   gcloud compute networks vpc-access connectors create $CONNECTOR_NAME --region=northamerica-northeast1 --network=default --range=10.8.0.0/28 --min-instances=2 --max-instances=10 --machine-type=e2-micro --project=$PROJECT_ID
   ```

### Solr Networking stuff
1. Create instance follower/leader instance groups
   
   ```
   FOLLOWER_GRP_NAME=namex-solr-follower-grp-$ENV
   ```
   
   ```
   gcloud compute instance-groups unmanaged create $FOLLOWER_GRP_NAME --project=$PROJECT_ID --zone=northamerica-northeast1-a
   ```
   
   ```
   gcloud compute instance-groups unmanaged set-named-ports $FOLLOWER_GRP_NAME --project=$PROJECT_ID --zone=northamerica-northeast1-a --named-ports=http:8983
   ```
   
   ```
   LEADER_GRP_NAME=namex-solr-leader-grp-$ENV
   ```
   
   ```
   gcloud compute instance-groups unmanaged create $LEADER_GRP_NAME --project=$PROJECT_ID --zone=northamerica-northeast1-a
   ```
   
   ```
   gcloud compute instance-groups unmanaged set-named-ports $LEADER_GRP_NAME --project=$PROJECT_ID --zone=northamerica-northeast1-a --named-ports=http:8983
   ```
2. Create the health check
   
   ```
   HEALTH_CHECK_NAME=namex-solr-hc-$ENV
   ```
   
   ```
   gcloud beta compute health-checks create tcp $HEALTH_CHECK_NAME --project=$PROJECT_ID --port=8983 --proxy-header=NONE --no-enable-logging --check-interval=5 --timeout=5 --unhealthy-threshold=2 --healthy-threshold=2
   ```
3. Add the firewall rule to allow the health check connection
   ```
   gcloud compute firewall-rules create solr-health-check --priority=1000 --direction=ingress --action=allow --source-ranges=35.191.0.0/16,130.211.0.0/22 --rules=tcp:80,tcp:8983 --project=$PROJECT_ID
   ```
4. Create a basic cloud armor policy
   - Navigation Menu/Cloud Armor (in the UI)
   - Create Policy
   - Fill in the name and leave the defaults / create
     *set the name in your shell for later*
      ```
      POLICY_NAME=
	  ```
   - Update the policy to allow the API connection
      ```
      ALLOW_API_RULE_NAME=allow-namex-solr-api-access-$ENV
	  ```
	 
      ```
      gcloud compute security-policies rules create 450 --project=$PROJECT_ID --action=allow --security-policy=$POLICY_NAME --src-ip-ranges=$STATIC_IP/32 --description=$ALLOW_API_RULE_NAME
	  ```
   - Update the policy to allow OCP connection
      ```
      ALLOW_OCP_RULE_NAME=allow-OCP-access-$ENV
	  ```
	 
	   ```
	  OCP_IP_RANGES=
	  ```
	 
      ```
      gcloud compute security-policies rules create 500 --project=$PROJECT_ID --action=allow --security-policy=$POLICY_NAME --src-ip-ranges=$OCP_IP_RANGES --description=$ALLOW_OCP_RULE_NAME
	  ```
1. Create the follwer/leader load balancers
   - Navigation Menu/Network Services/Load balancing (in the UI)
   - Create Load Balancer
     - Application Load Balancer (HTTP/S) -> next
     - Public facing -> next
     - Global -> next
     - Global external -> next
     - Create
     - Frontend
       - name proxy / keep defaults -> done
     - Backend
       - name service
       - set instance group (follower/leader)
       - set health check to what you created above
       - set cloud armor policy to what you created above
       - create
   - Create 
  *(repeat for remaining load balancers -- need one for follower and one for leader)*
### SOLR
1. Create the instance templates
   - set variables
     
     ```
     INSTANCE_TEMPLATE_FOLLOWER=namex-solr-follower-vm-tmpl-$ENV
     ```
    
     ```
     INSTANCE_TEMPLATE_LEADER=namex-solr-leader-vm-tmpl-$ENV
     ```
     *Set to the default compute service account EMAIL*
     ```
     SERVICE_ACCOUNT=$(gcloud iam service-accounts list --format="value(email)" --filter displayName:"Default compute service account" --project=$PROJECT_ID)
     ```
     
     ```
     TAGS=namex-solr
     ```
     
     ```
     DEVICE_NAME=namex-solr-disk-$ENV
     ```
     
     ```
     IMAGE_PATH=$PROJECT_ID/temp-namex
     ```
    
     ```
     IMAGE_FOLLOWER=$(gcloud artifacts docker images list northamerica-northeast1-docker.pkg.dev/$IMAGE_PATH --filter IMAGE:namex-solr-follower --format="value(IMAGE)" --limit=1):$ENV
     ```
    
     ```
     IMAGE_LEADER=$(gcloud artifacts docker images list northamerica-northeast1-docker.pkg.dev/$IMAGE_PATH --filter IMAGE:namex-solr-leader --format="value(IMAGE)" --limit=1):$ENV
     ```
     
     ```
     BOOT_DISK_IMAGE=cos-121-18867-199-34
     ```
     ```
     PATH_TO_STARTUP_SCRIPT=namex-solr/startupscript.txt
     ```
     
     For DEV (only has Leader instance)
     ```
     MACHINE_TYPE_LEADER=custom-1-5120
     ```
      ```
     BOOT_DISK_SIZE_LEADER=10GiB
     ```
     For TEST
     ```
     MACHINE_TYPE_FOLLOWER=custom-1-5120
     ```
     ```
     BOOT_DISK_SIZE_FOLLOWER=10GiB
     ```
      ```
     MACHINE_TYPE_LEADER=custom-1-5120
     ```
      ```
     BOOT_DISK_SIZE_LEADER=10GiB
     ```
     For SANDBOX
     ```
     MACHINE_TYPE_FOLLOWER=custom-1-6656
     ```
     ```
     BOOT_DISK_SIZE_FOLLOWER=16GiB
     ```
      ```
     MACHINE_TYPE_LEADER=custom-1-6656
     ```
      ```
     BOOT_DISK_SIZE_LEADER=24GiB
     ```
     For PROD
     ```
     MACHINE_TYPE_FOLLOWER=custom-1-8192-ext
     ```
     ```
     BOOT_DISK_SIZE_FOLLOWER=16GiB
     ```
      ```
     MACHINE_TYPE_LEADER=custom-2-10240
     ```
      ```
     BOOT_DISK_SIZE_LEADER=24GiB
     ```
	
   -  create the templates. UPDATE THE STARTUP SCRIPT BEFORE RUNNING
    ```
    gcloud compute instance-templates create $INSTANCE_TEMPLATE_FOLLOWER --project=$PROJECT_ID --machine-type=$MACHINE_TYPE_FOLLOWER --network-interface=network=default,network-tier=PREMIUM,stack-type=IPV4_ONLY --metadata=google-logging-enabled=true --metadata-from-file=startup-script=$PATH_TO_STARTUP_SCRIPT --maintenance-policy=MIGRATE --provisioning-model=STANDARD --service-account=$SERVICE_ACCOUNT --scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/trace.append --tags=$TAGS --create-disk=auto-delete=yes,boot=yes,device-name=$DEVICE_NAME,image=projects/cos-cloud/global/images/$BOOT_DISK_IMAGE,mode=rw,size=$BOOT_DISK_SIZE_FOLLOWER,type=pd-ssd --no-shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring --reservation-affinity=any
    ```
    UPDATE THE STARTUP SCRIPT AGAIN BEFORE RUNNING
   
    ```
    gcloud compute instance-templates create $INSTANCE_TEMPLATE_LEADER --project=$PROJECT_ID --machine-type=$MACHINE_TYPE_LEADER --network-interface=network=default,network-tier=PREMIUM,stack-type=IPV4_ONLY --metadata=google-logging-enabled=true --metadata-from-file=startup-script=$PATH_TO_STARTUP_SCRIPT --maintenance-policy=MIGRATE --provisioning-model=STANDARD --service-account=$SERVICE_ACCOUNT --scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/trace.append --tags=$TAGS --create-disk=auto-delete=yes,boot=yes,device-name=$DEVICE_NAME,image=projects/cos-cloud/global/images/$BOOT_DISK_IMAGE,mode=rw,size=$BOOT_DISK_SIZE_LEADER,type=pd-ssd --no-shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring --reservation-affinity=any
    ```
1. Update permissions to allow this environment to pull the image from tools
   ```
   gcloud projects add-iam-policy-binding $PROJECT--tools --member serviceAccount:$SERVICE_ACCOUNT --role=roles/artifactregistry.serviceAgent
   ```
2. *Deploy, create, and load the solr VMs

### Scheduler (for SOLR sync via API)

```
API_URL=$(gcloud run services describe search-api --platform managed --format 'value(status.url)' --region northamerica-northeast1 --project $PROJECT_ID)/api/v1/internal/solr/update/sync
```

```
gcloud scheduler jobs create http search-solr-sync-job-$ENV --schedule "*/3 * * * *" --uri $API_URL --http-method GET --location northamerica-northeast1 --project $PROJECT_ID
```

### Scheduler (for SOLR sync heartbeat via API)

```
API_URL=$(gcloud run services describe search-api --platform managed --format 'value(status.url)' --region northamerica-northeast1 --project $PROJECT_ID)/api/v1/internal/solr/update/sync/heartbeat
```

```
gcloud scheduler jobs create http search-solr-sync-heartbeat-job-$ENV --schedule "*/15 * * * *" --uri $API_URL --http-method GET --location northamerica-northeast1 --project $PROJECT_ID
```