# Deployment Guide — GCP VM (vm2)

## Prerequisites

- GCP VM `vm2` (zone `us-central1-c`, project `project-796df5af-a68e-4648-a8f`) with Docker installed
- Google service account JSON file on the VM
- Ports **8000**, **8001**, and **7860** open in the VM firewall

## 1. Build Docker images locally (for Linux VM)

Build all images for `linux/amd64` from your Mac:

```bash
docker buildx build --platform linux/amd64 -t expense-api ./expense-api
docker buildx build --platform linux/amd64 -t expense-mcp ./expense-mcp
docker buildx build --platform linux/amd64 -t langgraph-agent ./langgraph-agent
docker buildx build --platform linux/amd64 -t telegram-bot ./telegram-bot
```

## 2. Save and copy to VM

```bash
# Save images as tar files
docker save expense-api -o expense-api.tar
docker save expense-mcp -o expense-mcp.tar
docker save langgraph-agent -o langgraph-agent.tar
docker save telegram-bot -o telegram-bot.tar

# Copy to vm2
gcloud compute scp expense-api.tar vm2:~ --zone=us-central1-c --project=project-796df5af-a68e-4648-a8f
gcloud compute scp expense-mcp.tar vm2:~ --zone=us-central1-c --project=project-796df5af-a68e-4648-a8f
gcloud compute scp langgraph-agent.tar vm2:~ --zone=us-central1-c --project=project-796df5af-a68e-4648-a8f
gcloud compute scp telegram-bot.tar vm2:~ --zone=us-central1-c --project=project-796df5af-a68e-4648-a8f

# Copy service account JSON
gcloud compute scp /Users/shubham/Desktop/Projects/dark-quasar-329408-cb7c3cbf1f34.json vm2:~/service-account-key.json --zone=us-central1-c --project=project-796df5af-a68e-4648-a8f
```

## 3. Load images on VM

SSH into vm2:

```bash
gcloud compute ssh --zone "us-central1-c" "vm2" --project "project-796df5af-a68e-4648-a8f"
```

Load all images:

```bash
docker load -i expense-api.tar
docker load -i expense-mcp.tar
docker load -i langgraph-agent.tar
docker load -i telegram-bot.tar
```

## 4. Run containers

All containers use `--network host` so they can reach each other via `localhost`.

### Expense API (port 8000)

```bash
docker run -d \
  --network host \
  -e GOOGLE_SERVICE_ACCOUNT_FILE=/app/service_account.json \
  -e DATABASE_PATH=/app/data/users.db \
  -v /home/shubham/service-account-key.json:/app/service_account.json:ro \
  --name expense-api \
  expense-api
```

### Expense MCP Server (port 8001)

```bash
docker run -d \
  --network host \
  -e API_BASE_URL=http://localhost:8000 \
  --name expense-mcp \
  expense-mcp
```

### LangGraph Agent (port 7860)

```bash
docker run -d \
  --network host \
  -e ANTHROPIC_API_KEY=<YOUR_ANTHROPIC_API_KEY> \
  -e MCP_SERVER_URL=http://localhost:8001/mcp/ \
  -e MODEL_NAME=claude-haiku-4-5-20251001 \
  --name langgraph-agent \
  langgraph-agent
```

### Telegram Bot

```bash
docker run -d \
  --network host \
  -e TELEGRAM_BOT_TOKEN=<YOUR_TELEGRAM_BOT_TOKEN> \
  -e LANGFLOW_API_URL=http://localhost:7860/api/v1/run/e0a31254-b3c0-4801-80ce-c8ec1dd013ad \
  -e LANGFLOW_API_KEY=<YOUR_LANGFLOW_API_KEY> \
  -e ANTHROPIC_API_KEY=<YOUR_ANTHROPIC_API_KEY> \
  -e WHISPER_MODEL=small \
  -e VISION_MODEL=claude-haiku-4-5-20251001 \
  -e EXPENSE_API_URL=http://localhost:8000 \
  -e SERVICE_ACCOUNT_EMAIL=la-ferrari@dark-quasar-329408.iam.gserviceaccount.com \
  -e SESSION_MAX_MESSAGES=5 \
  --name telegram-bot \
  telegram-bot
```

## 5. Verify

```bash
# Check all containers are running
docker ps

# Test the API
curl http://localhost:8000/health

# Test the MCP endpoint
curl http://localhost:8001/mcp

# Test the LangGraph agent
curl http://localhost:7860/health

# Check Telegram bot logs
docker logs telegram-bot
```

## 6. Configure Claude Desktop (optional)

Update `claude_desktop_config.json` to point to the VM's external IP:

```json
"expense-tracker": {
    "command": "npx",
    "args": [
        "-y",
        "mcp-remote",
        "http://<VM_EXTERNAL_IP>:8001/mcp",
        "--allow-http"
    ]
}
```

## Open firewall ports (if needed)

```bash
gcloud compute firewall-rules create allow-expense-tracker \
    --allow tcp:8000,tcp:8001,tcp:7860 \
    --source-ranges 0.0.0.0/0 \
    --project=project-796df5af-a68e-4648-a8f \
    --description "Allow expense tracker API, MCP, and LangGraph agent"
```

## Useful commands

```bash
# View logs
docker logs -f expense-api
docker logs -f expense-mcp
docker logs -f langgraph-agent
docker logs -f telegram-bot

# Restart a container
docker restart expense-api
docker restart expense-mcp
docker restart langgraph-agent
docker restart telegram-bot

# Stop and remove a container
docker rm -f expense-api
docker rm -f expense-mcp
docker rm -f langgraph-agent
docker rm -f telegram-bot

# Stop and remove all containers
docker rm -f expense-api expense-mcp langgraph-agent telegram-bot

# Clean up old images and containers
docker system prune -a
```

## Redeployment (after code changes)

To redeploy a single service (e.g. expense-api):

```bash
# On your Mac:
docker buildx build --platform linux/amd64 -t expense-api ./expense-api
docker save expense-api -o expense-api.tar
gcloud compute scp expense-api.tar vm2:~ --zone=us-central1-c --project=project-796df5af-a68e-4648-a8f

# On the VM:
docker rm -f expense-api
docker load -i ~/expense-api.tar
docker run -d \
  --network host \
  -e GOOGLE_SERVICE_ACCOUNT_FILE=/app/service_account.json \
  -e DATABASE_PATH=/app/data/users.db \
  -v /home/shubham/service-account-key.json:/app/service_account.json:ro \
  --name expense-api \
  expense-api
```
