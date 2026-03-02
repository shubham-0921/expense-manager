# Changelog

## [Released]

### Eval Framework — End-to-End Agent Testing
- Added `evals/` directory with a full evaluation harness for the LangGraph agent
- `evals/test_cases.json` — 10 test cases (TC-001 to TC-010) covering simple expenses, payment methods, date resolution (yesterday, last Saturday), category inference, Splitwise split flow, expense summary, missing-amount handling, and large amounts
- `evals/assertions.py` — assertion engine supporting: `tools_called` (with `ordered` for sequence checks), `tools_not_called`, `tool_args` (partial match, list = "any of"), `tool_result_contains` (verifies MCP returned success, not just that the tool was called), `response_contains`, `response_not_contains`, `token_budget`
- `evals/run_evals.py` — main harness with per-test token breakdown (input/output separately), estimated cost per test case using Anthropic pricing table, and a summary line with score, avg tokens, total cost, budget violations, and runtime; exits non-zero if score < 90% or any token budget violation
- `evals/agent_client.py` — HTTP client for the `/eval/run` endpoint with `ToolCall`, `ToolResult`, and `AgentResponse` dataclasses
- Added `eval-up.sh` — one-command Docker environment startup: spins up Cloud SQL Auth Proxy, Expense API, Expense MCP, Splitwise MCP, and LangGraph Agent; includes `--no-build` and `--down` flags

### Agent Server — Eval Endpoint and Tool Result Tracing
- Added `POST /eval/run` to `langgraph-agent/server.py` — returns full tool call trace, tool results (ToolMessage content), multi-call token usage, and model name alongside the agent response
- Each eval run uses a fresh UUID session to prevent state leaking between test cases
- Token usage sums across all LLM calls in the ReAct trace (tool-selection call + final reply call); input and output tokens tracked separately since they are billed at different rates
- `tool_result_contains` assertion closes the gap where a test could pass even if the MCP tool returned an error but the agent hallucinated a confident confirmation

### Splitwise MCP Server - New Service
- Full Splitwise integration via MCP server with OAuth flow
- Create, view, and manage Splitwise expenses from Telegram
- Fuzzy matching for friends, groups, and categories
- Per-user token management with SQLite-backed token store

### Telegram Bot - Splitwise Integration
- Added `/connect_splitwise` to start OAuth flow
- Added `/splitwise_token` to save Splitwise token
- Added `/disconnect_splitwise` to unlink account
- Snap a photo of a bill and say who to split it with

### Bug Fix - Multi-User Token Race Condition
- Fixed race condition in Splitwise MCP `user_context.py` where concurrent requests would overwrite each other's tokens
- Replaced single shared `_fallback_access_token` variable with per-task dictionary keyed by `asyncio.current_task()` ID
- Previously caused "not in your friends list" errors for users when another request was in-flight

### Bug Fix - Corrupted Conversation History
- Agent errors (e.g. Splitwise API failures) would leave orphaned tool calls in LangGraph's conversation history
- Subsequent requests for the same session would permanently fail with `ValueError: Found AIMessages with tool_calls that do not have a corresponding ToolMessage`
- Now automatically clears session history on any agent error so the next request starts fresh

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

### Cost Optimization - Custom MCP Server & API Service

#### Phase 1: Off-the-shelf Google Sheets MCP Server (Baseline)
- Initial implementation used [`google-sheets-mcp-server`](https://github.com/xing5/mcp-google-sheets), an off-the-shelf server that exposes a broad set of generic tools for reading, writing, and managing Google Sheets
- The MCP server was Dockerised and deployed on a GCP VM in streamable-http mode, orchestrated via Langflow
- **Problem — High token usage:** Each request consumed ~65K tokens on Claude Opus 4.5, costing ~60 cents and taking ~2 minutes to respond; the orchestration only worked reliably with the Opus model
- Root causes identified:
  1. **Vague agent goals** — The agent received broad instructions and had to figure out sheet discovery, data reading, intent parsing, and writing all on its own, causing unnecessary LLM reasoning steps
  2. **Full sheet reads on every run** — The MCP server read the entire spreadsheet on every request, passing large amounts of raw data into the context window; switching to a fresh sheet brought usage down to ~44K tokens, confirming that data volume was a significant driver

#### Phase 2: Custom MCP Server (Current Architecture)
- **Core insight:** The LLM's job should be narrowly scoped to what it's best at — understanding natural language and extracting structured intent. All business logic (sheet reads, writes, data formatting) should live in the tool, not in the agent's reasoning loop
- Built a custom FastAPI-based expense API service that handles all Google Sheets logic internally, exposing only two focused domain endpoints (`add_expense`, `get_expense_summary`) instead of a dozen generic sheet-manipulation tools
- Built a custom MCP server wrapping those two endpoints — significantly reducing the tool schema surface area sent to the LLM on every request
- The LLM now only extracts structured data (amount, category, date) from the user's input; all Sheets read/write logic is encapsulated in the API service

#### Results
| Metric | Phase 1 (Off-the-shelf) | Phase 2 (Custom) | Improvement |
|---|---|---|---|
| Tokens per request | ~65K | ~20K (down to ~5K) | **~3–13x reduction** |
| Cost per request (Opus 4.5) | ~60 cents | ~10 cents | **~6x cheaper** |
| Cost per request (Haiku) | Not viable | ~2 cents | **~30x cheaper than Phase 1** |
| Models supported | Opus only | Opus, Sonnet, Haiku | Unlocked cheaper tiers |

- Migrating from off-the-shelf to custom MCP unblocked use of Claude Haiku — the lowest-cost model — bringing per-request cost down to ~2 cents, a **30x reduction** from the original 60 cents
