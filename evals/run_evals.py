#!/usr/bin/env python3
"""
Agent evaluation harness.

Usage:
    python evals/run_evals.py
    python evals/run_evals.py --filter date
    python evals/run_evals.py --splitwise-token <token> --save
    python evals/run_evals.py --base-url http://localhost:7860
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

# Allow plain imports of sibling modules regardless of working directory
sys.path.insert(0, str(Path(__file__).parent))

import httpx

from agent_client import AgentClient, AgentResponse
from assertions import run_assertions

# Anthropic pricing (USD per 1M tokens) — update when pricing changes
# https://www.anthropic.com/pricing
_PRICING: dict[str, tuple[float, float]] = {
    # model_id: (input_per_1M, output_per_1M)
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-haiku-4-5":          (0.80, 4.00),
    "claude-sonnet-4-5":         (3.00, 15.00),
    "claude-sonnet-4-6":         (3.00, 15.00),
    "claude-opus-4-6":           (15.00, 75.00),
}
_PRICING_FALLBACK = (3.00, 15.00)  # assume Sonnet if model unknown


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute estimated cost in USD for a single API interaction."""
    in_rate, out_rate = _PRICING.get(model, _PRICING_FALLBACK)
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


async def run_test_case(
    client: AgentClient, tc: dict, splitwise_token: str
) -> dict:
    tc_id = tc["id"]
    description = tc["description"]
    today = tc.get("today", datetime.now().strftime("%-d %B %Y"))
    user_id = tc.get("user_id", "99999")

    requires = tc.get("requires", [])
    if "splitwise" in requires and not splitwise_token:
        return {
            "id": tc_id,
            "description": description,
            "status": "skipped",
            "reason": "requires --splitwise-token",
            "assertions": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "llm_calls": 0,
            "response": "",
        }

    try:
        result: AgentResponse = await client.run(
            input_value=tc["input"],
            user_id=user_id,
            today=today,
            splitwise_token=splitwise_token if "splitwise" in requires else "",
        )
    except httpx.ConnectError:
        return {
            "id": tc_id,
            "description": description,
            "status": "error",
            "error": f"Could not connect to agent at {client.base_url}. Is the server running?",
            "assertions": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "llm_calls": 0,
            "response": "",
        }
    except Exception as e:
        return {
            "id": tc_id,
            "description": description,
            "status": "error",
            "error": str(e),
            "assertions": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "llm_calls": 0,
            "response": "",
        }

    assertion_results = run_assertions(
        result, tc.get("assertions", {}), tc.get("token_budget")
    )
    all_passed = all(a.passed for a in assertion_results)

    return {
        "id": tc_id,
        "description": description,
        "status": "pass" if all_passed else "fail",
        "assertions": [
            {"passed": a.passed, "description": a.description, "details": a.details}
            for a in assertion_results
        ],
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "total_tokens": result.total_tokens,
        "llm_calls": result.llm_calls,
        "cost_usd": _cost_usd(result.model, result.input_tokens, result.output_tokens),
        "response": result.response,
    }


def _tok(n: int) -> str:
    """Format a token count compactly, e.g. 2796 → '2,796'."""
    return f"{n:,}"


def print_report(results: list[dict], run_time: float) -> bool:
    """Print the formatted report. Returns True if the run is passing."""
    print(f"\nAgent Eval Results — {datetime.now().strftime('%d %b %Y %H:%M')}")
    print("─" * 80)

    passed_count = 0
    failed_count = 0
    skipped_count = 0
    token_violations = 0
    sum_input = 0
    sum_output = 0
    total_cost = 0.0

    for r in results:
        status = r["status"]
        label = f"{r['id']:<8} {r['description']:<42}"

        if status == "skipped":
            skipped_count += 1
            print(f"{label}  ~ SKIP  ({r['reason']})")
            continue

        if status == "error":
            failed_count += 1
            print(f"{label}  ✗ ERROR  {r['error']}")
            continue

        in_tok  = r.get("input_tokens", 0)
        out_tok = r.get("output_tokens", 0)
        calls   = r.get("llm_calls", 0)
        cost    = r.get("cost_usd", 0.0)
        sum_input  += in_tok
        sum_output += out_tok
        total_cost += cost

        tok_info = (
            f"  [{_tok(in_tok)} in / {_tok(out_tok)} out,"
            f" {calls} call{'s' if calls != 1 else ''},"
            f" ${cost:.4f}]"
        )
        n = len(r["assertions"])

        if status == "pass":
            passed_count += 1
            print(f"{label}  ✓ PASS  ({n} assertions){tok_info}")
        else:
            failed_count += 1
            failures = [a for a in r["assertions"] if not a["passed"]]
            fail_msgs = []
            for f in failures:
                msg = f["description"]
                if f["details"]:
                    msg += f": {f['details']}"
                fail_msgs.append(msg)
            print(f"{label}  ✗ FAIL  {'; '.join(fail_msgs)}{tok_info}")

        for a in r["assertions"]:
            if a["description"].startswith("token_budget") and not a["passed"]:
                token_violations += 1

    total = passed_count + failed_count
    score_pct = 100 * passed_count // total if total > 0 else 0
    avg_in  = sum_input  // total if total > 0 else 0
    avg_out = sum_output // total if total > 0 else 0

    print("─" * 80)
    print(
        f"Score: {passed_count}/{total} ({score_pct}%)"
        f"   Avg tokens: {_tok(avg_in)} in / {_tok(avg_out)} out"
        f"   Total cost: ${total_cost:.4f}"
        f"   Budget violations: {token_violations}"
        f"   Runtime: {run_time:.1f}s"
    )
    if skipped_count:
        print(f"Skipped: {skipped_count} (pass --splitwise-token to enable)")

    passing_run = score_pct >= 90 and token_violations == 0
    if total > 0:
        status_str = "✓ PASSING" if passing_run else "✗ BELOW THRESHOLD (need ≥90%)"
        print(f"Run status: {status_str}")

    return passing_run


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run agent evaluation suite")
    parser.add_argument(
        "--base-url", default="http://localhost:7860", help="Agent server base URL"
    )
    parser.add_argument(
        "--filter",
        metavar="PATTERN",
        help="Only run test cases whose id or description contains PATTERN",
    )
    parser.add_argument(
        "--splitwise-token", default="", metavar="TOKEN", help="Splitwise OAuth token"
    )
    parser.add_argument(
        "--save", action="store_true", help="Save results to evals/results/YYYY-MM-DD.json"
    )
    args = parser.parse_args()

    test_cases_path = Path(__file__).parent / "test_cases.json"
    with open(test_cases_path) as f:
        test_cases: list[dict] = json.load(f)

    if args.filter:
        pattern = args.filter.lower()
        test_cases = [
            tc
            for tc in test_cases
            if pattern in tc["id"].lower() or pattern in tc["description"].lower()
        ]
        print(f"Filtered to {len(test_cases)} test case(s) matching '{args.filter}'")

    client = AgentClient(base_url=args.base_url)
    start = datetime.now()
    results = []

    for tc in test_cases:
        print(f"  Running {tc['id']} ({tc['description']})...", end=" ", flush=True)
        r = await run_test_case(client, tc, args.splitwise_token)
        results.append(r)
        icon = {"pass": "✓", "fail": "✗", "skipped": "~", "error": "!"}.get(
            r["status"], "?"
        )
        print(icon)

    run_time = (datetime.now() - start).total_seconds()
    passing_run = print_report(results, run_time)

    if args.save:
        results_dir = Path(__file__).parent / "results"
        results_dir.mkdir(exist_ok=True)
        output_path = results_dir / f"{date.today().isoformat()}.json"
        with open(output_path, "w") as f:
            json.dump(
                {
                    "date": datetime.now().isoformat(),
                    "base_url": args.base_url,
                    "results": results,
                },
                f,
                indent=2,
            )
        print(f"\nResults saved to {output_path.relative_to(Path(__file__).parent.parent)}")

    sys.exit(0 if passing_run else 1)


if __name__ == "__main__":
    asyncio.run(main())
