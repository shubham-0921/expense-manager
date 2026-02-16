# Deployment Guide (Multi-User OAuth)

## Prerequisites

- Docker installed and running
- Splitwise OAuth app credentials (get from [secure.splitwise.com/apps](https://secure.splitwise.com/apps))

## 1. Register a Splitwise App

1. Go to [secure.splitwise.com/apps](https://secure.splitwise.com/apps)
2. Click **Register your application**
3. Fill in app name, description, etc.
4. Set the **Callback URL** to `http://localhost:8000/callback` (or your public server URL)
5. Note your **Consumer Key** and **Consumer Secret**

## 2. Build the Docker Image

```bash
cd splitwise-mcp
docker build -t splitwise-mcp .
```

## 3. Run the Container

```bash
docker run -d --name splitwise-mcp \
  -p 8000:8000 \
  -v splitwise-data:/data \
  -e SPLITWISE_CONSUMER_KEY=<> \
  -e SPLITWISE_CONSUMER_SECRET=<>> \
  -e SERVER_URL=http://localhost:8000 \
  splitwise-mcp
```

> The `-v splitwise-data:/data` mounts a named volume so user tokens persist across container restarts.

Verify it's running:

```bash
docker logs splitwise-mcp
```

You should see:

```
Splitwise MCP Server started (multi-user mode)
Uvicorn running on http://0.0.0.0:8000
```

## 4. Authorize Your Splitwise Account

1. Open **http://localhost:8000** in your browser
2. Click **"Connect your Splitwise account"**
3. You'll be redirected to Splitwise — authorize the app
4. After authorizing, you're redirected back to a success page showing your personal MCP URL:

```
http://localhost:8000/mcp?token=<your-unique-uuid>
```
https://example.com/callback?code=Z8REW7r6LWbwXk7yXqoI&state=

5. Copy this URL — you'll need it in the next step.

## 5. Connect to Claude Desktop

Open your Claude Desktop config file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add the following (replace the URL with your personal MCP URL from step 4):

```json
{
  "mcpServers": {
    "splitwise": {
      "type": "streamableHttp",
      "url": "http://localhost:8000/mcp?token=YOUR_TOKEN_UUID"
    }
  }
}
```

> If your Claude Desktop version doesn't support `streamableHttp` natively, use the mcp-remote bridge instead:
>
> ```json
> {
>   "mcpServers": {
>     "splitwise": {
>       "command": "npx",
>       "args": [
>         "-y",
>         "mcp-remote",
>         "http://localhost:8000/mcp?token=YOUR_TOKEN_UUID",
>         "--allow-http"
>       ]
>     }
>   }
> }
> ```

Restart Claude Desktop to pick up the new config.

## 6. Test It

Open Claude Desktop and try:

- "What Splitwise groups am I in?"
- "Show my recent expenses"
- "Who are my Splitwise friends?"
- "Add a $25 lunch expense split with John in the Roommates group"

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SPLITWISE_CONSUMER_KEY` | *(required)* | OAuth consumer key from Splitwise app |
| `SPLITWISE_CONSUMER_SECRET` | *(required)* | OAuth consumer secret from Splitwise app |
| `SERVER_URL` | `http://localhost:8000` | Public URL of the server (used for OAuth redirect) |
| `TOKEN_DB_PATH` | `/data/tokens.db` | Path to SQLite database for user tokens |
| `SPLITWISE_CACHE_TTL` | `86400` | Cache TTL in seconds (24 hours) |
| `MCP_TRANSPORT` | `streamable-http` | Transport type (`stdio` or `streamable-http`) |
| `MCP_HOST` | `0.0.0.0` | Host to bind to |
| `MCP_PORT` | `8000` | Port to listen on |

## Container Management

```bash
# Stop the server
docker stop splitwise-mcp

# Start it again (tokens persist in the volume)
docker start splitwise-mcp

# View logs
docker logs -f splitwise-mcp

# Remove the container (volume/tokens are preserved)
docker rm -f splitwise-mcp

# Remove the container AND wipe all tokens
docker rm -f splitwise-mcp && docker volume rm splitwise-data

# Rebuild after code changes
docker build -t splitwise-mcp . && \
  docker rm -f splitwise-mcp && \
  docker run -d --name splitwise-mcp \
    -p 8000:8000 \
    -v splitwise-data:/data \
    -e SPLITWISE_CONSUMER_KEY=YOUR_CONSUMER_KEY \
    -e SPLITWISE_CONSUMER_SECRET=YOUR_CONSUMER_SECRET \
    -e SERVER_URL=http://localhost:8000 \
    splitwise-mcp
```

## Multi-User Support

Each user who visits `/authorize` and completes the OAuth flow gets their own unique MCP URL with a `?token=<uuid>` parameter. This means:

- Multiple people can connect their Splitwise accounts to the same server
- Each user's requests are isolated — tools operate on their own Splitwise data
- Tokens are stored in SQLite at `TOKEN_DB_PATH` and persist across restarts
