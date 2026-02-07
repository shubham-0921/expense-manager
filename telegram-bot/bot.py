import base64
import logging
import os
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

anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

logger.info(f"Loading Whisper model '{WHISPER_MODEL}'...")
whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
logger.info("Whisper model loaded.")


async def check_user_registered(user_id: int) -> bool:
    """Check if a user is registered by calling the expense-api."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{EXPENSE_API_URL}/user/{user_id}", timeout=5)
    return resp.status_code == 200


async def register_user(user_id: int, spreadsheet_id: str, sheet_name: str = "Sheet1") -> str:
    """Register a user's Google Sheet via the expense-api."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{EXPENSE_API_URL}/register",
            json={
                "telegram_user_id": str(user_id),
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
            },
            timeout=5,
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


async def call_langflow(text: str, user_id: str) -> str:
    headers = {"Content-Type": "application/json"}
    if LANGFLOW_API_KEY:
        headers["x-api-key"] = LANGFLOW_API_KEY

    payload = {
        "input_value": text,
        "output_type": "chat",
        "input_type": "chat",
        "session_id": get_session_id(user_id),
        "user_id": user_id,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(LANGFLOW_API_URL, json=payload, headers=headers, timeout=30)

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    share_instruction = ""
    if SERVICE_ACCOUNT_EMAIL:
        share_instruction = f"\n2. Share it with: {SERVICE_ACCOUNT_EMAIL} (Editor access)\n3."
    else:
        share_instruction = "\n2. Share it with the bot's service account email (ask the admin)\n3."

    await update.message.reply_text(
        "Hey! I'm your expense tracker bot.\n\n"
        "To get started:\n"
        "1. Create a Google Sheet for your expenses"
        f"{share_instruction} Run /setup <your-spreadsheet-id>\n\n"
        "Your spreadsheet ID is the long string in the Google Sheets URL:\n"
        "docs.google.com/spreadsheets/d/<THIS-PART>/edit\n\n"
        "Once set up, send me expenses like:\n"
        '  "spent 300 on lunch at swiggy"\n'
        '  "paid 1500 for uber using axis select"\n\n'
        "Or send a voice message or a photo of a receipt/bill!\n\n"
        "Or ask:\n"
        '  "how much did I spend recently?"\n'
        '  "show my summary"'
    )


async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setup <spreadsheet_id> [sheet_name] command."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /setup <spreadsheet-id> [sheet-name]\n\n"
            "Example: /setup 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms\n"
            "Example: /setup 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms MyExpenses\n\n"
            "Your spreadsheet ID is the long string in the URL:\n"
            "docs.google.com/spreadsheets/d/<THIS-PART>/edit"
        )
        return

    spreadsheet_id = context.args[0]
    sheet_name = context.args[1] if len(context.args) > 1 else "Sheet1"
    user_id = update.effective_user.id

    await update.message.chat.send_action("typing")

    result = await register_user(user_id, spreadsheet_id, sheet_name)

    if result == "success":
        share_msg = ""
        if SERVICE_ACCOUNT_EMAIL:
            share_msg = f"\n\nMake sure you've shared your Google Sheet with:\n{SERVICE_ACCOUNT_EMAIL}"

        await update.message.reply_text(
            f"You're all set! Your expenses will be tracked in your Google Sheet.{share_msg}\n\n"
            "Try sending an expense like:\n"
            '  "spent 300 on lunch at swiggy"'
        )
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Bot started. Polling for messages...")
    app.run_polling()


if __name__ == "__main__":
    main()
