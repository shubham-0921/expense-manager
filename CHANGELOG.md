# Changelog

## [Released]

### Telegram Bot - Faster Whisper Migration
- Replaced `openai-whisper` (PyTorch) with `faster-whisper` (CTranslate2)
- Docker image reduced from ~4.5GB to ~1GB
- 2-4x faster transcription on CPU with similar accuracy
- Uses `int8` quantization for minimal memory usage

### Telegram Bot - Daily Summary
- Added `/summary_on` and `/summary_off` commands
- Sends monthly spending summary (total, count, by category) daily at configurable time
- Immediate summary sent on opt-in
- Configurable via `DAILY_SUMMARY_HOUR` and `DAILY_SUMMARY_MINUTE` env vars (default: 14:30 UTC)

### Telegram Bot - Periodic Expense Reminders
- Added `/remind_on` and `/remind_off` commands
- Sends periodic nudges to log expenses with varied messages
- Only sends during waking hours (configurable start/end hour)
- Immediate reminder sent on opt-in
- Configurable via `REMINDER_INTERVAL_HOURS`, `REMINDER_START_HOUR_UTC`, `REMINDER_END_HOUR_UTC` env vars

### Telegram Bot - Pinned Google Sheet Link
- After `/setup`, the bot sends and pins the Google Sheet URL in chat
- Users can always access their expense sheet from the pinned message

### Telegram Bot - User Name Capture
- Captures user's Telegram display name during `/setup`
- Stores name in the database alongside registration info

### Telegram Bot - Job Queue Support
- Added `python-telegram-bot[job-queue]` dependency for scheduled tasks
- Powers both daily summaries and periodic reminders
