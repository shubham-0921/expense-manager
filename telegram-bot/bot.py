import base64
import datetime
import logging
import os
import random
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import anthropic
import httpx
from faster_whisper import WhisperModel
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
LANGFLOW_API_URL = os.environ["LANGFLOW_API_URL"]
LANGFLOW_API_KEY = os.environ.get("LANGFLOW_API_KEY", "")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
VISION_MODEL = os.environ.get("VISION_MODEL", "claude-haiku-4-5-20251001")

# expense-api base URL for registration
EXPENSE_API_URL = os.environ.get("EXPENSE_API_URL", "http://localhost:8000")

# Service account email to display in /setup instructions
SERVICE_ACCOUNT_EMAIL = os.environ.get("SERVICE_ACCOUNT_EMAIL", "")

# Splitwise MCP server URL (for OAuth link)
SPLITWISE_MCP_URL = os.environ.get("SPLITWISE_MCP_URL", "")

# Daily summary time (hour, minute) in UTC
DAILY_SUMMARY_HOUR = int(os.environ.get("DAILY_SUMMARY_HOUR", "14"))  # 14 UTC = 7:30 PM IST
DAILY_SUMMARY_MINUTE = int(os.environ.get("DAILY_SUMMARY_MINUTE", "30"))

# Track users who opted in for daily summaries: {user_id: chat_id}
summary_subscribers: dict[int, int] = {}

# Expense reminder settings
REMINDER_INTERVAL_HOURS = int(os.environ.get("REMINDER_INTERVAL_HOURS", "2"))
REMINDER_START_HOUR_UTC = int(os.environ.get("REMINDER_START_HOUR_UTC", "3"))   # 3 UTC = 8:30 AM IST
REMINDER_END_HOUR_UTC = int(os.environ.get("REMINDER_END_HOUR_UTC", "16"))      # 16 UTC = 9:30 PM IST

# Track users who opted in for reminders: {user_id: chat_id}
reminder_subscribers: dict[int, int] = {}

REMINDER_MESSAGES = [
    "Had any expenses today? Drop them here before you forget!",
    "Quick check-in: any spending to log since last time?",
    "Bought anything recently? Send it over — text, voice, or photo!",
    "Don't let expenses pile up! Log them while they're fresh.",
    "Friendly nudge: any bills, meals, or purchases to track?",
    "Keeping your expenses up to date? Send me what you've spent!",
    "Any coffee, food, or shopping to log? I'm here when you're ready.",
]

anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

logger.info(f"Loading Whisper model '{WHISPER_MODEL}'...")
whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
logger.info("Whisper model loaded.")


async def check_user_registered(user_id: int) -> bool:
    """Check if a user is registered by calling the expense-api."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{EXPENSE_API_URL}/user/{user_id}", timeout=30)
    return resp.status_code == 200


async def register_user(user_id: int, spreadsheet_id: str, sheet_name: str = "Sheet1", name: str = "") -> str:
    """Register a user's Google Sheet via the expense-api."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{EXPENSE_API_URL}/register",
            json={
                "telegram_user_id": str(user_id),
                "name": name,
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
            },
            timeout=30,
        )
    if resp.status_code == 200:
        return "success"
    return resp.text


SESSION_MAX_MESSAGES = int(os.environ.get("SESSION_MAX_MESSAGES", "5"))
# Track per-user sessions: {user_id: {"session_id": str, "count": int}}
user_sessions: dict[str, dict] = {}


def get_session_id(user_id: str) -> str:
    """Return the same session_id for up to SESSION_MAX_MESSAGES, then rotate."""
    session = user_sessions.get(user_id)
    if not session or session["count"] >= SESSION_MAX_MESSAGES:
        user_sessions[user_id] = {"session_id": str(uuid.uuid4()), "count": 0}
    user_sessions[user_id]["count"] += 1
    return user_sessions[user_id]["session_id"]


async def get_user_splitwise_token(user_id: str) -> str:
    """Fetch the user's Splitwise MCP token from the expense API."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{EXPENSE_API_URL}/splitwise-token/{user_id}", timeout=10)
        if resp.status_code == 200:
            return resp.json().get("splitwise_token", "")
    except Exception:
        pass
    return ""


async def call_langflow(text: str, user_id: str) -> str:
    headers = {"Content-Type": "application/json"}
    if LANGFLOW_API_KEY:
        headers["x-api-key"] = LANGFLOW_API_KEY

    # Fetch Splitwise token if available
    splitwise_token = await get_user_splitwise_token(user_id)

    payload = {
        "input_value": text,
        "output_type": "chat",
        "input_type": "chat",
        "session_id": get_session_id(user_id),
        "user_id": user_id,
        "splitwise_token": splitwise_token,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(LANGFLOW_API_URL, json=payload, headers=headers, timeout=60)

    if resp.status_code != 200:
        logger.error(f"Langflow error: {resp.status_code} {resp.text}")
        return "Sorry, something went wrong processing your request."

    data = resp.json()

    # Extract the agent's response text from Langflow output
    try:
        outputs = data["outputs"][0]["outputs"][0]
        message = outputs["results"]["message"]["text"]
        return message
    except (KeyError, IndexError):
        logger.error(f"Unexpected Langflow response format: {data}")
        return str(data)


async def fetch_monthly_summary(user_id: str) -> str | None:
    """Fetch the current month's summary from the expense API."""
    month = datetime.date.today().strftime("%Y-%m")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{EXPENSE_API_URL}/summary",
            params={"user_id": user_id, "month": month},
            timeout=20,
        )
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data["count"] == 0:
        return None
    lines = [f"Daily Summary ({datetime.date.today().strftime('%d %b %Y')})"]
    lines.append(f"This month: {data['count']} expenses, total: {data['total']}")
    if data.get("by_category"):
        lines.append("\nBy category:")
        for cat, amt in sorted(data["by_category"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {cat}: {amt}")
    return "\n".join(lines)


async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: send daily summary to all subscribers."""
    for user_id, chat_id in summary_subscribers.items():
        try:
            summary = await fetch_monthly_summary(str(user_id))
            if summary:
                await context.bot.send_message(chat_id=chat_id, text=summary)
        except Exception as e:
            logger.error(f"Failed to send daily summary to {user_id}: {e}")


async def send_expense_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: send periodic expense reminders to subscribers."""
    now_utc = datetime.datetime.now(datetime.timezone.utc).hour
    if not (REMINDER_START_HOUR_UTC <= now_utc < REMINDER_END_HOUR_UTC):
        return
    for user_id, chat_id in reminder_subscribers.items():
        try:
            msg = random.choice(REMINDER_MESSAGES)
            await context.bot.send_message(chat_id=chat_id, text=msg)
        except Exception as e:
            logger.error(f"Failed to send reminder to {user_id}: {e}")


async def remind_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remind_on — opt in to periodic expense reminders."""
    user_id = update.effective_user.id
    if not await check_user_registered(user_id):
        await update.message.reply_text("You need to /setup first before enabling reminders.")
        return
    reminder_subscribers[user_id] = update.effective_chat.id
    await update.message.reply_text(
        f"Reminders enabled! I'll nudge you every {REMINDER_INTERVAL_HOURS}h "
        f"between {REMINDER_START_HOUR_UTC:02d}:00–{REMINDER_END_HOUR_UTC:02d}:00 UTC to log expenses.\n"
        "Use /remind_off to disable."
    )
    await update.message.reply_text(random.choice(REMINDER_MESSAGES))


async def remind_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remind_off — opt out of periodic reminders."""
    user_id = update.effective_user.id
    reminder_subscribers.pop(user_id, None)
    await update.message.reply_text("Reminders disabled.")


async def summary_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /summary_on — opt in to daily summaries."""
    user_id = update.effective_user.id
    if not await check_user_registered(user_id):
        await update.message.reply_text("You need to /setup first before enabling daily summaries.")
        return
    summary_subscribers[user_id] = update.effective_chat.id
    await update.message.reply_text(
        f"Daily summary enabled! You'll receive a spending summary every day at "
        f"{DAILY_SUMMARY_HOUR:02d}:{DAILY_SUMMARY_MINUTE:02d} UTC.\n"
        "Use /summary_off to disable."
    )
    summary = await fetch_monthly_summary(str(user_id))
    if summary:
        await update.message.reply_text(summary)
    else:
        await update.message.reply_text("No expenses logged this month yet. Start tracking!")


async def summary_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /summary_off — opt out of daily summaries."""
    user_id = update.effective_user.id
    summary_subscribers.pop(user_id, None)
    await update.message.reply_text("Daily summary disabled.")


async def connect_splitwise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /connect_splitwise — start Splitwise OAuth flow."""
    user_id = update.effective_user.id
    if not await check_user_registered(user_id):
        await update.message.reply_text("You need to /setup first before connecting Splitwise.")
        return
    if not SPLITWISE_MCP_URL:
        await update.message.reply_text("Splitwise integration is not configured on this server.")
        return

    authorize_url = f"{SPLITWISE_MCP_URL}/authorize"
    await update.message.reply_text(
        "To connect your Splitwise account:\n\n"
        f"1. Open this link: {authorize_url}\n"
        "2. Authorize the app on Splitwise\n"
        "3. You'll see a success page with your personal MCP URL\n"
        "4. Copy the token from the URL (the part after ?token=)\n"
        "5. Send it back here with:\n"
        "   /splitwise_token <your-token>\n\n"
        "Example:\n"
        "/splitwise_token a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    )


async def set_splitwise_token_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /splitwise_token <uuid> — save user's Splitwise token."""
    user_id = update.effective_user.id
    if not await check_user_registered(user_id):
        await update.message.reply_text("You need to /setup first.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /splitwise_token <your-token-uuid>")
        return

    token = context.args[0].strip()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{EXPENSE_API_URL}/splitwise-token",
            json={"telegram_user_id": str(user_id), "splitwise_token": token},
            timeout=30,
        )

    if resp.status_code == 200:
        await update.message.reply_text(
            "Splitwise connected!\n\n"
            "You can now use Splitwise features, like:\n"
            '  "show my Splitwise groups"\n'
            '  "add a 500 dinner split with John on Splitwise"\n'
            '  "what do I owe on Splitwise?"'
        )
    else:
        await update.message.reply_text(f"Failed to save token: {resp.text}")


async def disconnect_splitwise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /disconnect_splitwise — remove Splitwise token."""
    user_id = update.effective_user.id
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"{EXPENSE_API_URL}/splitwise-token/{user_id}", timeout=30)

    if resp.status_code == 200:
        await update.message.reply_text("Splitwise disconnected.")
    else:
        await update.message.reply_text("No Splitwise connection found.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    share_instruction = ""
    if SERVICE_ACCOUNT_EMAIL:
        share_instruction = f"\n2. Share it with: {SERVICE_ACCOUNT_EMAIL} (Editor access)\n3."
    else:
        share_instruction = "\n2. Share it with the bot's service account email (ask the admin)\n3."

    await update.message.reply_text(
        "Hey! I'm Expense Manager — your personal expense tracking assistant.\n\n"
        "I help you manage your expenses in natural language using Text, Voice, or Photos. "
        "All your data lives in your own Google Sheet. Only the service account has access "
        "to your sheet — not even the person who built me can see it.\n\n"
        "To get started:\n"
        "1. Create a Google Sheet for your expenses"
        f"{share_instruction} Run /setup <your-google-sheet-link>\n\n"
        "Just paste the full link from your browser, e.g.:\n"
        "/setup https://docs.google.com/spreadsheets/d/1BxiMVs.../edit\n\n"
        "Once set up, send me expenses like:\n"
        '  "spent 300 on lunch at swiggy"\n'
        '  "paid 1500 for uber using axis select"\n\n'
        "Or send a voice message or a photo of a receipt/bill!\n\n"
        "Or ask:\n"
        '  "how much did I spend recently?"\n'
        '  "show my summary"\n\n'
        "Splitwise integration:\n"
        "  /connect_splitwise — link your Splitwise account\n"
        "  /disconnect_splitwise — unlink Splitwise"
    )


def extract_spreadsheet_id(input_str: str) -> str | None:
    """Extract spreadsheet ID from a Google Sheets URL or return the raw ID."""
    import re
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", input_str)
    if match:
        return match.group(1)
    # If it looks like a raw ID (alphanumeric, hyphens, underscores), return as-is
    if re.fullmatch(r"[a-zA-Z0-9_-]+", input_str):
        return input_str
    return None


async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setup <spreadsheet-url-or-id> [sheet-name] command."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /setup <google-sheet-link> [sheet-name]\n\n"
            "Example: /setup https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit\n"
            "Example: /setup https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit MyExpenses"
        )
        return

    spreadsheet_id = extract_spreadsheet_id(context.args[0])
    if not spreadsheet_id:
        await update.message.reply_text(
            "That doesn't look like a valid Google Sheet link or ID. "
            "Please paste the full URL from your browser's address bar."
        )
        return
    sheet_name = context.args[1] if len(context.args) > 1 else "Sheet1"
    user_id = update.effective_user.id
    user = update.effective_user
    name = " ".join(filter(None, [user.first_name, user.last_name]))

    await update.message.chat.send_action("typing")

    result = await register_user(user_id, spreadsheet_id, sheet_name, name)

    if result == "success":
        share_msg = ""
        if SERVICE_ACCOUNT_EMAIL:
            share_msg = f"\n\nMake sure you've shared your Google Sheet with:\n{SERVICE_ACCOUNT_EMAIL}"

        await update.message.reply_text(
            f"You're all set! Your expenses will be tracked in your Google Sheet.{share_msg}\n\n"
            "Try sending an expense like:\n"
            '  "spent 300 on lunch at swiggy"'
        )

        # Send and pin the Google Sheet link for easy access
        sheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
        link_msg = await update.message.reply_text(f"Your expense sheet:\n{sheet_url}")
        try:
            await link_msg.pin(disable_notification=True)
        except Exception as e:
            logger.warning(f"Could not pin sheet link: {e}")
    else:
        await update.message.reply_text(f"Registration failed: {result}\nPlease try again.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await check_user_registered(user_id):
        await update.message.reply_text(
            "You haven't set up your Google Sheet yet!\n"
            "Run /setup <spreadsheet-id> to get started.\n"
            "Use /start for detailed instructions."
        )
        return

    user_text = update.message.text
    logger.info(f"User {user_id}: {user_text}")

    await update.message.chat.send_action("typing")

    response = await call_langflow(user_text, str(user_id))
    await update.message.reply_text(response)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await check_user_registered(user_id):
        await update.message.reply_text(
            "You haven't set up your Google Sheet yet!\n"
            "Run /setup <spreadsheet-id> to get started."
        )
        return

    await update.message.chat.send_action("typing")

    # Download voice file
    voice = update.message.voice or update.message.audio
    file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

    try:
        # Transcribe with Whisper
        logger.info(f"Transcribing voice from user {user_id}")
        segments, _ = whisper_model.transcribe(tmp_path)
        transcript = " ".join(seg.text for seg in segments).strip()
        logger.info(f"Transcript: {transcript}")

        if not transcript:
            await update.message.reply_text("Couldn't understand the audio. Please try again.")
            return

        await update.message.reply_text(f"Heard: \"{transcript}\"\nProcessing...")

        # Send transcript to Langflow agent
        response = await call_langflow(transcript, str(user_id))
        await update.message.reply_text(response)
    finally:
        os.unlink(tmp_path)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await check_user_registered(user_id):
        await update.message.reply_text(
            "You haven't set up your Google Sheet yet!\n"
            "Run /setup <spreadsheet-id> to get started."
        )
        return

    await update.message.chat.send_action("typing")

    # Download the highest-resolution photo
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

    try:
        # Read and base64-encode the image
        with open(tmp_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        # Extract expense details from the receipt using Claude Vision
        logger.info(f"Extracting receipt details from photo by user {user_id}")

        message = await anthropic_client.messages.create(
            model=VISION_MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Look at this image and determine if it is a receipt, bill, or invoice.\n\n"
                                "If it IS a receipt/bill/invoice, extract the details and respond with ONLY a natural language expense description in this format:\n"
                                "\"spent [total amount] on [category] at [merchant/vendor] on [date], paid via [payment method]\"\n\n"
                                "Rules:\n"
                                "- Amount: The final/total amount paid. Use just the number (e.g. 450, not Rs. 450)\n"
                                "- Category: One of: food, groceries, transport, shopping, subscriptions, recharge, rent, utilities, entertainment, health, travel, other\n"
                                "- Merchant: The store/restaurant/vendor name\n"
                                "- Date: The transaction date from the receipt (e.g. '5 Feb' or '5 February 2025')\n"
                                "- Payment method: If visible (e.g. UPI, cash, credit card). Omit 'paid via ...' if not visible\n"
                                "- If multiple items, add a brief comment (e.g. 'comment: milk, bread, eggs')\n\n"
                                "Examples:\n"
                                "- spent 450 on groceries at DMart on 5 Feb, paid via UPI\n"
                                "- spent 1200 on food at Starbucks on 3 February 2025\n"
                                "- spent 350 on groceries at BigBasket on 7 Feb, paid via credit card, comment: fruits and vegetables\n\n"
                                "If it is NOT a receipt/bill/invoice, respond with exactly: NOT_A_RECEIPT\n"
                                "If you cannot read the amount clearly, respond with exactly: UNCLEAR_AMOUNT"
                            ),
                        },
                    ],
                }
            ],
        )

        extracted_text = message.content[0].text.strip()
        logger.info(f"Vision extraction result: {extracted_text}")

        if extracted_text == "NOT_A_RECEIPT":
            await update.message.reply_text(
                "That doesn't look like a receipt or bill. "
                "Please send a photo of a receipt, invoice, or bill to log an expense."
            )
            return

        if extracted_text == "UNCLEAR_AMOUNT":
            await update.message.reply_text(
                "I can see this is a receipt, but I couldn't read the amount clearly. "
                "Could you try sending a clearer photo, or just type the expense details?"
            )
            return

        # Append user's caption (if any) so the agent gets extra context
        caption = update.message.caption
        if caption:
            extracted_text = f"{extracted_text}, {caption}"

        await update.message.reply_text(f"Extracted: \"{extracted_text}\"\nProcessing...")

        # Send extracted text to Langflow agent
        response = await call_langflow(extracted_text, str(user_id))
        await update.message.reply_text(response)

    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}")
        await update.message.reply_text("Sorry, I couldn't process that image. Please try again or type the expense details.")
    finally:
        os.unlink(tmp_path)


def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", setup))
    app.add_handler(CommandHandler("summary_on", summary_on))
    app.add_handler(CommandHandler("summary_off", summary_off))
    app.add_handler(CommandHandler("remind_on", remind_on))
    app.add_handler(CommandHandler("remind_off", remind_off))
    app.add_handler(CommandHandler("connect_splitwise", connect_splitwise))
    app.add_handler(CommandHandler("splitwise_token", set_splitwise_token_cmd))
    app.add_handler(CommandHandler("disconnect_splitwise", disconnect_splitwise))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Schedule daily summary job
    summary_time = datetime.time(hour=DAILY_SUMMARY_HOUR, minute=DAILY_SUMMARY_MINUTE)
    app.job_queue.run_daily(send_daily_summary, time=summary_time)
    logger.info(f"Daily summary scheduled at {summary_time} UTC")

    # Schedule periodic expense reminders
    app.job_queue.run_repeating(send_expense_reminder, interval=REMINDER_INTERVAL_HOURS * 3600)
    logger.info(f"Expense reminders scheduled every {REMINDER_INTERVAL_HOURS}h ({REMINDER_START_HOUR_UTC:02d}:00–{REMINDER_END_HOUR_UTC:02d}:00 UTC)")

    async def post_init(application):
        await application.bot.set_my_commands([
            ("start", "Get started with the bot"),
            ("setup", "Link your Google Sheet"),
            ("connect_splitwise", "Connect your Splitwise account"),
            ("splitwise_token", "Save your Splitwise token"),
            ("disconnect_splitwise", "Unlink Splitwise"),
            ("summary_on", "Enable daily spending summary"),
            ("summary_off", "Disable daily summary"),
            ("remind_on", "Enable expense reminders"),
            ("remind_off", "Disable reminders"),
        ])
        await application.bot.set_my_description(
            "I'm Expense Manager — your personal expense tracking assistant. "
            "I help you manage your expenses in natural language using Text, Voice, or Photos. "
            "All your data lives in your own Google Sheet. Only the service account has access "
            "to your sheet — not even the person who built me can see it.\n\n"
            "Tap START to get going!"
        )

    app.post_init = post_init

    logger.info("Bot started. Polling for messages...")
    app.run_polling()


if __name__ == "__main__":
    main()
