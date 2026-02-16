import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).parent / "agent-goal.md").read_text()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp/")
SPLITWISE_MCP_BASE_URL = os.getenv("SPLITWISE_MCP_BASE_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "claude-haiku-4-5-20251001")

# Static resources (created once at startup)
llm = None
expense_tools = None
memory = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global llm, expense_tools, memory

    llm = ChatAnthropic(
        model=MODEL_NAME,
        anthropic_api_key=ANTHROPIC_API_KEY,
    )

    # Load expense-tracker tools (static, no per-user auth)
    mcp_client = MultiServerMCPClient(
        {
            "expense-tracker-mcp-server": {
                "url": MCP_SERVER_URL,
                "transport": "streamable_http",
            }
        }
    )
    expense_tools = await mcp_client.get_tools()

    memory = MemorySaver()

    logger.info(f"Agent ready with {len(expense_tools)} expense tools: {[t.name for t in expense_tools]}")
    if SPLITWISE_MCP_BASE_URL:
        logger.info(f"Splitwise MCP configured at {SPLITWISE_MCP_BASE_URL}")

    yield


async def _run_agent(input_value: str, user_id: str, session_id: str, splitwise_token: str = "") -> str:
    """Build the tool list, create an agent, and run the query."""
    today = datetime.now().strftime("%-d %B %Y")
    message_content = f"[user_id: {user_id}] [today: {today}] {input_value}" if user_id else input_value

    # Always include expense-tracker tools
    tools = list(expense_tools)

    # Add Splitwise tools if user has a token and server is configured
    # Keep sw_client reference alive so MCP sessions persist during agent execution
    sw_client = None
    if splitwise_token and SPLITWISE_MCP_BASE_URL:
        try:
            splitwise_url = f"{SPLITWISE_MCP_BASE_URL}?token={splitwise_token}"
            sw_client = MultiServerMCPClient(
                {
                    "splitwise": {
                        "url": splitwise_url,
                        "transport": "streamable_http",
                    }
                }
            )
            sw_tools = await sw_client.get_tools()
            tools.extend(sw_tools)
            logger.info(f"Loaded {len(sw_tools)} Splitwise tools for user {user_id}")
        except Exception as e:
            logger.warning(f"Failed to load Splitwise tools for user {user_id}: {e}")

    agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT, checkpointer=memory)
    config = {"configurable": {"thread_id": session_id}}

    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=message_content)]},
            config=config,
        )
    except Exception:
        # Any error during agent execution may leave the conversation
        # history in a corrupted state (e.g. orphaned tool_calls without
        # ToolMessages). Clear the session so the next request starts fresh.
        logger.exception(f"Agent error for session {session_id}, clearing history")
        if session_id in memory.storage:
            del memory.storage[session_id]
        raise

    return result["messages"][-1].content


app = FastAPI(title="Expense Tracker Agent", lifespan=lifespan)


@app.post("/api/v1/run/{flow_id}")
async def run_flow(flow_id: str, request: Request):
    """Langflow-compatible endpoint — Telegram bot calls this."""
    body = await request.json()
    input_value = body.get("input_value", "")
    user_id = body.get("user_id", "")
    session_id = body.get("session_id", user_id or "default")
    splitwise_token = body.get("splitwise_token", "")

    response_text = await _run_agent(input_value, user_id, session_id, splitwise_token)

    # Match Langflow's response format for backward compatibility
    return {
        "outputs": [
            {
                "outputs": [
                    {
                        "results": {
                            "message": {"text": response_text}
                        }
                    }
                ]
            }
        ]
    }


@app.post("/run")
async def run_simple(request: Request):
    """Simpler endpoint for direct use."""
    body = await request.json()
    input_value = body.get("input_value", "")
    user_id = body.get("user_id", "")
    session_id = body.get("session_id", user_id or "default")
    splitwise_token = body.get("splitwise_token", "")

    response_text = await _run_agent(input_value, user_id, session_id, splitwise_token)
    return {"response": response_text}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7860)
