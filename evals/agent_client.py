from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:7860"


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    name: str
    result: str


@dataclass
class AgentResponse:
    response: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    model: str = ""


class AgentClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 90.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def run(
        self,
        input_value: str,
        user_id: str = "99999",
        today: str = "",
        splitwise_token: str = "",
    ) -> AgentResponse:
        payload: dict[str, Any] = {
            "input_value": input_value,
            "user_id": user_id,
            "splitwise_token": splitwise_token,
        }
        if today:
            payload["today"] = today
        else:
            payload["today"] = datetime.now().strftime("%-d %B %Y")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/eval/run", json=payload)
            resp.raise_for_status()
            data = resp.json()

        tool_calls = [
            ToolCall(name=tc["name"], args=tc["args"])
            for tc in data.get("tool_calls", [])
        ]
        tool_results = [
            ToolResult(name=tr["name"], result=tr["result"])
            for tr in data.get("tool_results", [])
        ]
        usage = data.get("token_usage", {})
        return AgentResponse(
            response=data["response"],
            tool_calls=tool_calls,
            tool_results=tool_results,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            llm_calls=usage.get("llm_calls", 0),
            model=data.get("model", ""),
        )
