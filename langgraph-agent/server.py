import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

load_dotenv()

SYSTEM_PROMPT = """\
You are a personal expense tracking assistant. You help the user log expenses and review their spending.

## Tools Available
- **add_expense**: Log a new expense
- **get_expense_summary**: View recent spending summary

## Behavior

When the user mentions spending money or buying something:
1. Extract: amount, category, date, payment method, comment, and whether to split
2. If amount or category is unclear, ask before logging
3. Call add_expense with the extracted details
4. Confirm what was added in a short, friendly message

When the user asks about their spending:
1. Call get_expense_summary with an appropriate last_n value
2. Present the summary in a clean, readable format

## Field Guidelines
- **category**: Map to one of: food, groceries, transport, shopping, subscriptions, recharge, rent, utilities, entertainment, health, travel, other
- **date**: Use format like "6 February". If not mentioned, leave blank (defaults to today)
- **payment_method**: Common values: upi, cash, rupay credit card, axis select, hdfc cc. If not mentioned, leave blank
- **split_with**: Person's name if splitting, otherwise "none"
- **comment**: Brief note about what the expense was for

## Examples

User: "spent 300 on lunch at magnolia bakery, split with mishra"
→ add_expense(amount=300, category="food", comment="magnolia bakery", split_with="mishra")

User: "paid 1500 for uber to airport using axis select"
→ add_expense(amount=1500, category="transport", comment="uber to airport", payment_method="axis select")

User: "how much did I spend recently?"
→ get_expense_summary(last_n=10)

## Tone
Be concise and conversational. Use ₹ for amounts. Don't over-explain."""

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

    agent_executor = create_react_agent(
        llm,
        tools,
        prompt=SYSTEM_PROMPT
    )

    print(f"Agent ready with {len(tools)} tools: {[t.name for t in tools]}")

    yield

    # No cleanup needed anymore



app = FastAPI(title="Expense Tracker Agent", lifespan=lifespan)


@app.post("/api/v1/run/{flow_id}")
async def run_flow(flow_id: str, request: Request):
    """Langflow-compatible endpoint — Telegram bot works without changes."""
    body = await request.json()
    input_value = body.get("input_value", "")

    result = await agent_executor.ainvoke(
        {"messages": [HumanMessage(content=input_value)]}
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

    result = await agent_executor.ainvoke(
        {"messages": [HumanMessage(content=input_value)]}
    )

    return {"response": result["messages"][-1].content}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7860)