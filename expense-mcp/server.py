import os

import httpx
from fastmcp import FastMCP

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

mcp = FastMCP(
    "Expense Tracker",
    instructions=(
        "You are an expense tracking assistant. Use add_expense to log expenses "
        "and get_expense_summary to view recent spending. Always confirm what was "
        "added after logging an expense."
    ),
)


@mcp.tool
async def add_expense(
    amount: float,
    category: str,
    date: str = "",
    payment_method: str = "",
    comment: str = "",
    split_with: str = "none",
) -> str:
    """Add an expense to the tracker.

    Args:
        amount: The expense amount (e.g. 300)
        category: Category like food, groceries, transport, shopping, subscriptions, etc.
        date: Date of expense (e.g. '18 January'). Defaults to today if not provided.
        payment_method: How it was paid (e.g. 'rupay credit card', 'axis select', 'cash', 'upi')
        comment: What the expense was for (e.g. 'magnolia bakery', 'uber to airport')
        split_with: Person to split with, or 'none' if not splitting
    """
    payload = {"amount": amount, "category": category}
    if date:
        payload["date"] = date
    if payment_method:
        payload["payment_method"] = payment_method
    if comment:
        payload["comment"] = comment
    if split_with:
        payload["split_with"] = split_with

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{API_BASE_URL}/expense", json=payload, timeout=10)

    if resp.status_code != 200:
        return f"Failed to add expense: {resp.text}"

    data = resp.json()
    expense = data.get("expense", {})

    parts = [f"Added expense: ₹{expense.get('amount', amount)} for {expense.get('category', category)}"]
    if expense.get("date"):
        parts.append(f"Date: {expense['date']}")
    if expense.get("payment_method"):
        parts.append(f"Paid via: {expense['payment_method']}")
    if expense.get("comment"):
        parts.append(f"Note: {expense['comment']}")
    if data.get("split_info"):
        parts.append(f"Split: {data['split_info']}")
    parts.append(f"Row #{data.get('row', '?')}")

    return "\n".join(parts)


@mcp.tool
async def get_expense_summary(last_n: int = 5) -> str:
    """Get a summary of recent expenses.

    Args:
        last_n: Number of recent expenses to summarize (default 5)
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API_BASE_URL}/summary", params={"last_n": last_n}, timeout=10)

    if resp.status_code != 200:
        return f"Failed to get summary: {resp.text}"

    data = resp.json()

    lines = [f"Last {data.get('count', last_n)} expenses — Total: ₹{data.get('total', 0)}"]

    by_category = data.get("by_category", {})
    if by_category:
        lines.append("\nBy category:")
        for cat, amt in sorted(by_category.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {cat}: ₹{amt}")

    by_payment = data.get("by_payment_method", {})
    if by_payment:
        lines.append("\nBy payment method:")
        for method, amt in sorted(by_payment.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {method}: ₹{amt}")

    pending = data.get("pending_splits", [])
    if pending:
        lines.append("\nPending splits:")
        for s in pending:
            lines.append(f"  {s.get('date', '?')} — ₹{s.get('to_collect', '?')} from {s.get('split_with', '?')} ({s.get('comment', '')})")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)
