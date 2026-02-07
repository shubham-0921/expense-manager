# Telegram Expense Tracker Bot

A Telegram bot that accepts text and voice messages to log expenses via a Langflow AI agent. Voice messages are transcribed locally using OpenAI Whisper.

## Architecture

```
Telegram App (phone/desktop)
    │
    ▼
Telegram Bot (this service, runs locally)
    │
    ├── Text message ─────────────────┐
    │                                  ▼
    └── Voice message ──► Whisper ──► Langflow Agent (localhost:7860)
                          (local)          │
                                           ▼
                                      MCP Server (VM:8001)
                                           │
                                           ▼
                                      FastAPI (VM:8000)
                                           │
                                           ▼
                                      Google Sheets
```

## Prerequisites

- **Python 3.10+**
- **ffmpeg** — required by Whisper for audio processing
- **Langflow** running locally with the expense tracker agent configured
- **MCP Server + FastAPI** running on the VM (see root `deployment.md`)
- A **Telegram bot token** from [@BotFather](https://t.me/BotFather)

## Setup

### 1. Install ffmpeg

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

### 2. Create a virtual environment

```bash
cd telegram-bot
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `openai-whisper` downloads model weights on first run (~150MB for `small`). If you hit an SSL error on macOS, run:
> ```bash
> export SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())")
> ```

### 4. Configure environment variables

Copy and edit the `.env` file:

```bash
cp .env.example .env   # or edit .env directly
```

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `LANGFLOW_API_URL` | Yes | Langflow flow run endpoint (e.g. `http://localhost:7860/api/v1/run/<flow-id>`) |
| `LANGFLOW_API_KEY` | No | Langflow API key (if authentication is enabled) |
| `WHISPER_MODEL` | No | Whisper model size: `tiny`, `base`, `small`, `medium`, `large` (default: `base`) |
| `ALLOWED_USER_IDS` | No | Comma-separated Telegram user IDs to restrict access (empty = allow all) |

**Whisper model sizes:**

| Model | Size | Speed | Accuracy |
|---|---|---|---|
| `tiny` | 39 MB | Fastest | Low |
| `base` | 74 MB | Fast | OK |
| `small` | 244 MB | Medium | Good (recommended) |
| `medium` | 769 MB | Slow | Better |
| `large` | 1.5 GB | Slowest | Best |

### 5. Run the bot

```bash
python bot.py
```

You should see:

```
INFO:__main__:Loading Whisper model 'small'...
INFO:__main__:Whisper model loaded.
INFO:__main__:Bot started. Polling for messages...
```

The bot is now running and will respond to messages in Telegram.

## Usage

### Text messages

Send natural language expense descriptions:

```
spent 300 on lunch at swiggy
paid 1500 for uber using axis select
how much did I spend recently?
show my summary
```

### Voice messages

Record a voice message in Telegram describing your expense. The bot will:
1. Download the audio
2. Transcribe it using Whisper (shows "Heard: ..." confirmation)
3. Send the transcript to the Langflow agent
4. Reply with the agent's response

### Commands

| Command | Description |
|---|---|
| `/start` | Show welcome message and usage examples |

## How It Works

1. **Text flow:** User sends text → bot forwards to Langflow API → Langflow agent calls MCP tools → response sent back to user
2. **Voice flow:** User sends voice → bot downloads `.ogg` file → Whisper transcribes locally → transcript sent to Langflow → same as text flow
3. **Session isolation:** Each message gets a unique `session_id` (UUID) to prevent Langflow context buildup and hallucination after many messages
4. **Authorization:** If `ALLOWED_USER_IDS` is set, only those Telegram user IDs can interact with the bot

## Troubleshooting

### Bot not responding
- Check that the bot is running (`python bot.py`)
- Verify `TELEGRAM_BOT_TOKEN` is correct
- Make sure you messaged the right bot in Telegram

### "Sorry, something went wrong processing your request."
- Langflow is not running or unreachable
- Check `LANGFLOW_API_URL` is correct
- Check Langflow logs for errors

### Voice transcription is empty or wrong
- Upgrade the Whisper model: set `WHISPER_MODEL=small` or `medium` in `.env`
- Make sure `ffmpeg` is installed: `ffmpeg -version`
- Speak clearly and avoid very short recordings

### SSL certificate error when downloading Whisper model
```bash
export SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())")
pip install certifi
```

### Expenses not reaching Google Sheets
- Check that MCP server and FastAPI are running on the VM: `docker compose ps`
- Check VM container logs: `docker compose logs --tail=50`
- Verify Langflow is actually calling MCP tools (check Langflow output panel)
- If it worked before then stopped — Langflow session context may be too long. The `session_id` fix should prevent this.

### Finding your Telegram user ID
Send a message to [@userinfobot](https://t.me/userinfobot) on Telegram — it will reply with your user ID.

## Running in background (optional)

To keep the bot running after closing the terminal:

```bash
# Using nohup
nohup python bot.py > bot.log 2>&1 &

# Or using screen
screen -S expense-bot
python bot.py
# Press Ctrl+A, then D to detach

# To reattach:
screen -r expense-bot
```

## File Structure

```
telegram-bot/
├── bot.py              # Main bot code
├── .env                # Environment variables (not committed)
├── requirements.txt    # Python dependencies
├── Dockerfile          # Docker image definition
└── README.md           # This file
```
