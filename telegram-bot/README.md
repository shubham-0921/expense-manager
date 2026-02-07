# Telegram Expense Tracker Bot

A Telegram bot that accepts text, voice messages, and receipt photos to log expenses via a LangGraph AI agent. Voice messages are transcribed locally using faster-whisper, and receipt photos are parsed using Claude Vision.

## Architecture

```
Telegram App (phone/desktop)
    │
    ├── Text message ─────────────────────────┐
    │                                          ▼
    ├── Voice message ──► faster-whisper ──► LangGraph Agent (localhost:7860)
    │                      (local STT)            │
    └── Photo (receipt) ──► Claude Vision ────────┤
                            (Anthropic API)       ▼
                                             MCP Server (localhost:8001)
                                                  │
                                                  ▼
                                             FastAPI (localhost:8000)
                                                  │
                                                  ▼
                                             Google Sheets
```

## Prerequisites

- **Python 3.10+**
- **ffmpeg** — required by faster-whisper for audio processing
- **LangGraph agent** running (see `langgraph-agent/`)
- **MCP Server + FastAPI** running (see root `deployment.md`)
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

### 4. Configure environment variables

Copy and edit the `.env` file:

```bash
cp .env.example .env   # or edit .env directly
```

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `LANGFLOW_API_URL` | Yes | LangGraph agent endpoint (e.g. `http://localhost:7860/api/v1/run/<flow-id>`) |
| `LANGFLOW_API_KEY` | No | API key for the agent endpoint (if authentication is enabled) |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude Vision (receipt parsing) |
| `VISION_MODEL` | No | Claude model for receipt parsing (default: `claude-haiku-4-5-20251001`) |
| `WHISPER_MODEL` | No | faster-whisper model size: `tiny`, `base`, `small`, `medium`, `large-v3` (default: `base`) |
| `EXPENSE_API_URL` | No | Base URL of the expense API for user registration (default: `http://localhost:8000`) |
| `SERVICE_ACCOUNT_EMAIL` | No | Google service account email shown in /setup instructions |
| `SESSION_MAX_MESSAGES` | No | Messages per session before rotating session ID (default: `5`) |

**Whisper model sizes (faster-whisper):**

| Model | Speed | Accuracy |
|---|---|---|
| `tiny` | Fastest | Low |
| `base` | Fast | OK |
| `small` | Medium | Good (recommended) |
| `medium` | Slow | Better |
| `large-v3` | Slowest | Best |

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

## Usage

### Commands

| Command | Description |
|---|---|
| `/start` | Show welcome message and setup instructions |
| `/setup <spreadsheet-id> [sheet-name]` | Register your Google Sheet for expense tracking |

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
2. Transcribe it using faster-whisper (shows "Heard: ..." confirmation)
3. Send the transcript to the LangGraph agent
4. Reply with the agent's response

### Receipt photos

Send a photo of a receipt, bill, or invoice. The bot will:
1. Download the highest-resolution photo
2. Extract expense details using Claude Vision (shows "Extracted: ..." confirmation)
3. Send the extracted text to the LangGraph agent
4. Reply with the agent's response

Non-receipt photos are rejected with a helpful message.

## How It Works

1. **User registration:** Users run `/setup <spreadsheet-id>` to register their Google Sheet. The bot calls the expense API to store the mapping.
2. **Text flow:** User sends text → bot forwards to LangGraph agent → agent calls MCP tools → expense logged to Google Sheets → response sent back
3. **Voice flow:** User sends voice → bot downloads `.ogg` → faster-whisper transcribes locally → transcript sent to agent → same as text flow
4. **Photo flow:** User sends photo → bot downloads image → Claude Vision extracts expense details → extracted text sent to agent → same as text flow
5. **Session rotation:** Messages share a session ID for up to `SESSION_MAX_MESSAGES` (default 5), then the session rotates to prevent context buildup. The LangGraph agent uses this for conversation memory within a session.

## Troubleshooting

### Bot not responding
- Check that the bot is running (`python bot.py`)
- Verify `TELEGRAM_BOT_TOKEN` is correct
- Make sure you messaged the right bot in Telegram

### "You haven't set up your Google Sheet yet!"
- Run `/setup <spreadsheet-id>` with your Google Sheet ID
- Make sure the expense API is running and reachable at `EXPENSE_API_URL`

### "Sorry, something went wrong processing your request."
- LangGraph agent is not running or unreachable
- Check `LANGFLOW_API_URL` is correct
- Check agent logs: `docker logs langgraph-agent`

### Voice transcription is empty or wrong
- Upgrade the Whisper model: set `WHISPER_MODEL=small` or `medium` in `.env`
- Make sure `ffmpeg` is installed: `ffmpeg -version`
- Speak clearly and avoid very short recordings

### Receipt photo not recognized
- Make sure the photo is clear and well-lit
- The image should show a receipt, bill, or invoice with visible amounts
- If Claude Vision can't read the amount, it will ask for a clearer photo
- You can always type the expense details manually instead

### Expenses not reaching Google Sheets
- Check that MCP server and FastAPI are running: `docker ps`
- Check container logs: `docker logs expense-api`, `docker logs expense-mcp`
- Make sure you've shared your Google Sheet with the service account email

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
