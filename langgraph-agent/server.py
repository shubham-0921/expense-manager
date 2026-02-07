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

SYSTEM_PROMPT = (Path(__file__).parent / "agent-goal.md").read_text()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp/")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "claude-haiku-4-5-20251001")

agent_executor = None
mcp_client_instance = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_executor, mcp_client_instance

    llm = ChatAnthropic(
        model=MODEL_NAME,
        anthropic_api_key=ANTHROPIC_API_KEY
    )

    mcp_client_instance = MultiServerMCPClient(
        {
            "expense-tracker-mcp-server": {
                "url": MCP_SERVER_URL,
                "transport": "streamable_http",
            }
        }
    )

    # IMPORTANT: await get_tools() (no context manager)
    tools = await mcp_client_instance.get_tools()

    memory = MemorySaver()
    agent_executor = create_react_agent(
        llm,
        tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=memory,
    )

    print(f"Agent ready with {len(tools)} tools: {[t.name for t in tools]}")

    yield

    # No cleanup needed anymore



app = FastAPI(title="Expense Tracker Agent", lifespan=lifespan)


@app.post("/api/v1/run/{flow_id}")
async def run_flow(flow_id: str, request: Request):
    """Langflow-compatible endpoint — Telegram bot calls this."""
    body = await request.json()
    input_value = body.get("input_value", "")
    user_id = body.get("user_id", "")
    session_id = body.get("session_id", user_id or "default")

    # Prefix user_id and today's date so the agent can use them
    today = datetime.now().strftime("%-d %B %Y")
    message_content = f"[user_id: {user_id}] [today: {today}] {input_value}" if user_id else input_value

    result = await agent_executor.ainvoke(
        {"messages": [HumanMessage(content=message_content)]},
        config={"configurable": {"thread_id": session_id}},
    )

    response_text = result["messages"][-1].content

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

    today = datetime.now().strftime("%-d %B %Y")
    message_content = f"[user_id: {user_id}] [today: {today}] {input_value}" if user_id else input_value

    result = await agent_executor.ainvoke(
        {"messages": [HumanMessage(content=message_content)]},
        config={"configurable": {"thread_id": session_id}},
    )

    return {"response": result["messages"][-1].content}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7860)
