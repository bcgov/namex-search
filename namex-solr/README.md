# Application Name

BC Registries NameX Solr

## Technology Stack Used

- Apache Solr
- Docker

### Development Setup

1. Pull the base solr docker image

- `podman pull solr:9.8.1`

2. Run your solr containers

- if first time or need to pickup new solr changes outside of /solr/name_request directory:
  - Build leader image: `make build-local`
  - If you use podman instead docker, you need to
  - Run leader image (note this command suits for docker only, podman will generate empty IPAddress): `docker run -d -p 8863:8983 --name name-request-solr-leader-local name-request-solr-local` (it will be available on port 8863)
    _NOTE: if you want the data to persist then add `-v $PWD/solr/name-request:/var/solr/data` (do NOT do this for the solr instance used for api unit tests)_
    _NOTE: if you use podman instead of docker and you want a container-accessible IP, create a user-defined bridge network by command 'sudo podman network create mynet', then run 'sudo podman run --network mynet -d -p 8863:8983 --name name-request-solr-leader-local name-request-solr-local'
  - Optional: setup follower node
    - Get leader IP: `podman inspect name-request-solr-leader-local | grep IPAddress`
    - Use the docker IP to set the leader url: `export LEADER_URL=http://leader_IP:8983/solr/name_request`  (remember to change leader_IP to your IPAddress found in the last step, and update allowUrls in solr.xml accordingly with this IPAddress)
    - Build the follower image: `make build-follower`
    - Run follower image: `podman run -d -p 8864:8983 --name name-request-solr-follower-local name-request-solr-follower` (it will be available on port 8864)
    - Add docker network so that follower can poll from leader:
      - `podman network create solr`
      - `podman network connect solr name-request-solr-leader-local`
      - `podman network connect solr name-request-solr-follower-local`
- else
  - `podman start name-request-solr-leader-local`

3. Check logs for errors

- `podman logs name-request-solr-leader-local`

4. Go to admin UI in browser and check the solr core is there (it will be empty)

- http://localhost:8863/solr
