# Deployment Guide — GCP VM

## Prerequisites

- A GCP VM instance (e.g. `mcp-server-1` in `us-central1-a`)
- Docker installed on the VM
- Google service account JSON file
- Ports **8000** and **8001** open in the VM firewall

## 1. Copy files to VM

```bash
# Copy the project
gcloud compute scp --recurse /Users/shubham/Desktop/Projects/custom-expense-tracker-mcp-server mcp-server-1:~ --zone=us-central1-a

# Copy the service account JSON
gcloud compute scp /Users/shubham/Desktop/Projects/dark-quasar-329408-cb7c3cbf1f34.json mcp-server-1:~ --zone=us-central1-a
```

## 2. Install Docker Compose on the VM

SSH into the VM:

```bash
gcloud compute ssh mcp-server-1 --zone=us-central1-a
```

Install the Docker Compose plugin:

```bash
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

Verify:

```bash
docker compose version
```

## 3. Build and run

```bash
cd ~/custom-expense-tracker-mcp-server

export GOOGLE_SERVICE_ACCOUNT_FILE=~/dark-quasar-329408-cb7c3cbf1f34.json

docker compose up --build -d
```

## 4. Verify

```bash
# Check containers are running
docker compose ps

# Check logs
docker compose logs -f

# Test the API
curl http://localhost:8000/health

# Test the MCP endpoint
curl http://localhost:8001/mcp
```

## 5. Configure Claude Desktop

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

## 6. Deploy LangGraph Agent

### Build locally (for Linux VM)

```bash
docker buildx build --platform linux/amd64 -t langgraph-agent ./langgraph-agent
```

### Copy to VM

```bash
docker save langgraph-agent -o langgraph-agent.tar
gcloud compute scp langgraph-agent.tar mcp-server-1:~ --zone=us-central1-a
```

### Load and run on VM

```bash
gcloud compute ssh mcp-server-1 --zone=us-central1-a
docker load -i ~/langgraph-agent.tar
docker run -d \
  --network host \
  -e ANTHROPIC_API_KEY=<your-anthropic-api-key> \
  -e MCP_SERVER_URL=http://localhost:8001/mcp/ \
  -e MODEL_NAME=claude-haiku-4-5-20251001 \
  --name langgraph-agent \
  langgraph-agent
```

### Verify

```bash
curl http://localhost:7860/health
```

## 7. Deploy Telegram Bot

### Build locally (for Linux VM)

```bash
docker buildx build --platform linux/amd64 -t telegram-bot ./telegram-bot
```

### Copy to VM

```bash
docker save telegram-bot -o telegram-bot.tar
gcloud compute scp telegram-bot.tar mcp-server-1:~ --zone=us-central1-a
```

### Load and run on VM

```bash
gcloud compute ssh mcp-server-1 --zone=us-central1-a
docker load -i ~/telegram-bot.tar
docker run -d \
  --network host \
  -e TELEGRAM_BOT_TOKEN=<your-telegram-bot-token> \
  -e LANGFLOW_API_URL=http://localhost:7860/api/v1/run/expense-tracker \
  -e WHISPER_MODEL=base \
  --name telegram-bot \
  telegram-bot
```

### Verify

```bash
docker logs telegram-bot
```

## Open firewall ports (if needed)

```bash
gcloud compute firewall-rules update allow-expense-tracker \
    --allow tcp:8000,tcp:8001,tcp:7860 \
    --description "Allow expense tracker API, MCP, and LangGraph agent"
```

## Useful commands

```bash
# Stop services
docker compose down

# Rebuild after code changes
docker compose up --build -d

# View logs for a specific service
docker compose logs -f expense-api
docker compose logs -f expense-mcp

# Restart a single service
docker compose restart expense-mcp

# View LangGraph agent logs
docker logs -f langgraph-agent

# View Telegram bot logs
docker logs -f telegram-bot

# Restart LangGraph agent
docker restart langgraph-agent

# Restart Telegram bot
docker restart telegram-bot

# Stop and remove LangGraph agent
docker rm -f langgraph-agent

# Stop and remove Telegram bot
docker rm -f telegram-bot
```

## Open firewall ports (if needed)

```bash
gcloud compute firewall-rules create allow-expense-tracker \
    --allow tcp:8000,tcp:8001 \
    --source-ranges 0.0.0.0/0 \
    --description "Allow expense tracker API and MCP"
```
