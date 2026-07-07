# Infrastructure_Repo

Docker orchestration for the full WFC system: MQTT broker, commander (central + backup), swarm (2 leaders + scout + fighter), dashboard, and the integration test orchestrator.

## Run

```bash
cd Infrastructure_Repo
docker compose up --build
```

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:8080 |
| Map view | http://localhost:8081 |
| Test console | http://localhost:9090 |
| MQTT broker | localhost:1883 |

Teardown:
```bash
docker compose down -v
```

## Layout

```
<PROJECT_ROOT>/
  Commander_Repo/
  Swarm_Repo/
  Dashboard_Repo/
  Infrastructure_Repo/      <- you are here
    docker-compose.yml
    docker/mosquitto.conf
  wfc_shared/
  Integration_Tests/
```
