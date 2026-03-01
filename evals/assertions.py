from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_client import AgentResponse, ToolResult


@dataclass
class AssertionResult:
    passed: bool
    description: str
    details: str = ""


def run_assertions(
    result: AgentResponse,
    assertions: dict[str, Any],
    token_budget: int | None = None,
) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    tool_names = [tc.name for tc in result.tool_calls]

    # tools_called — order-independent unless "ordered": true
    expected_tools: list[str] = assertions.get("tools_called", [])
    if expected_tools:
        if assertions.get("ordered"):
            passed = _is_subsequence(tool_names, expected_tools)
            results.append(AssertionResult(
                passed=passed,
                description=f"tool_order: {' → '.join(expected_tools)}",
                details="" if passed else f"actual={tool_names}",
            ))
        else:
            for tool in expected_tools:
                passed = tool in tool_names
                results.append(AssertionResult(
                    passed=passed,
                    description=f"tool_called: {tool}",
                    details="" if passed else f"called={tool_names}",
                ))

    # tools_not_called
    for tool in assertions.get("tools_not_called", []):
        passed = tool not in tool_names
        results.append(AssertionResult(
            passed=passed,
            description=f"tool_not_called: {tool}",
            details="" if passed else "tool was unexpectedly called",
        ))

    # tool_args — partial match; list values mean "any of"
    for tool_name, expected_args in assertions.get("tool_args", {}).items():
        matching = [tc for tc in result.tool_calls if tc.name == tool_name]
        if not matching:
            results.append(AssertionResult(
                passed=False,
                description=f"tool_args: {tool_name}",
                details=f"{tool_name} was never called",
            ))
            continue
        # Check against the last matching call
        tc = matching[-1]
        for key, expected_val in expected_args.items():
            actual_val = tc.args.get(key)
            passed = _values_match(actual_val, expected_val)
            results.append(AssertionResult(
                passed=passed,
                description=f"tool_args: {tool_name}.{key}",
                details="" if passed else f"expected={expected_val!r}, actual={actual_val!r}",
            ))

    # response_contains — case-insensitive substring
    response_lower = result.response.lower()
    for substr in assertions.get("response_contains", []):
        passed = substr.lower() in response_lower
        results.append(AssertionResult(
            passed=passed,
            description=f"response_contains: {substr!r}",
            details="" if passed else "not found in response",
        ))

    # response_not_contains
    for substr in assertions.get("response_not_contains", []):
        passed = substr.lower() not in response_lower
        results.append(AssertionResult(
            passed=passed,
            description=f"response_not_contains: {substr!r}",
            details="" if passed else "found in response",
        ))

    # tool_result_contains — verify the MCP tool actually returned expected content
    for tool_name, expected in assertions.get("tool_result_contains", {}).items():
        matching: list[ToolResult] = [tr for tr in result.tool_results if tr.name == tool_name]
        if not matching:
            results.append(AssertionResult(
                passed=False,
                description=f"tool_result_contains: {tool_name}",
                details=f"{tool_name} returned no result",
            ))
            continue
        tr = matching[-1]
        substrings = expected if isinstance(expected, list) else [expected]
        for substr in substrings:
            passed = substr.lower() in tr.result.lower()
            results.append(AssertionResult(
                passed=passed,
                description=f"tool_result_contains: {tool_name}",
                details="" if passed else f"expected {substr!r} in: {tr.result[:120]!r}",
            ))

    # token_budget
    if token_budget is not None:
        passed = result.input_tokens <= token_budget
        results.append(AssertionResult(
            passed=passed,
            description=f"token_budget: ≤{token_budget}",
            details="" if passed else f"used {result.input_tokens} tokens",
        ))

    return results


def _is_subsequence(seq: list[str], subseq: list[str]) -> bool:
    """True if all elements of subseq appear in seq in the given relative order."""
    it = iter(seq)
    return all(item in it for item in subseq)


def _values_match(actual: Any, expected: Any) -> bool:
    """
    Flexible comparison:
    - list expected: passes if actual matches any item in the list
    - numeric expected: compare as floats
    - string expected: case-insensitive
    - bool expected: exact
    """
    if actual is None:
        return False
    if isinstance(expected, list):
        return any(_values_match(actual, e) for e in expected)
    if isinstance(expected, bool):
        return actual == expected
    if isinstance(expected, (int, float)):
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    if isinstance(expected, str):
        return str(actual).lower() == expected.lower()
    return actual == expected
