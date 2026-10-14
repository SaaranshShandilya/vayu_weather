from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis_tools import TOOL_BY_NAME, build_analysis
from backend.app import routing_alignment_issue
from backend.config import parse_api_keys
from backend.database import AirQualityDatabase, database as app_database
from backend.gemini_service import GeminiService, RoutedAnalysis
from backend.keypool import KeyPool
from backend.models import ChatMessage
from evals.practical_cases import PRACTICAL_CASES


def is_subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and is_subset(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and expected == actual
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        return abs(expected - actual) < 1e-9
    return expected == actual


def match_calls(
    expected: list[dict[str, Any]],
    actual: RoutedAnalysis,
    expected_outcome: str,
) -> tuple[bool, list[str]]:
    if expected_outcome == "abstain":
        terminal = {"clarify_question", "out_of_scope"}
        if len(actual.calls) == 1 and actual.calls[0].name in terminal:
            return True, []

    problems: list[str] = []
    unmatched = list(actual.calls)
    for expected_call in expected:
        expected_arguments = expected_call.get("arguments", {})

        def arguments_match(actual_call: Any) -> bool:
            if actual_call.name != expected_call["name"]:
                return False
            if actual_call.name != "relationship":
                return is_subset(expected_arguments, actual_call.arguments)
            expected_metrics = {
                expected_arguments.get("x_metric"),
                expected_arguments.get("y_metric"),
            }
            actual_metrics = {
                actual_call.arguments.get("x_metric"),
                actual_call.arguments.get("y_metric"),
            }
            remaining = {
                key: value
                for key, value in expected_arguments.items()
                if key not in {"x_metric", "y_metric"}
            }
            return (
                expected_metrics == actual_metrics
                and is_subset(remaining, actual_call.arguments)
            )

        matching_index = next(
            (
                index
                for index, actual_call in enumerate(unmatched)
                if arguments_match(actual_call)
            ),
            None,
        )
        if matching_index is None:
            problems.append(
                f"missing {expected_call['name']} with "
                f"{expected_call.get('arguments', {})}"
            )
        else:
            unmatched.pop(matching_index)
    if unmatched:
        problems.append(
            "unexpected calls: "
            + ", ".join(
                f"{call.name}({call.arguments})"
                for call in unmatched
            )
        )
    return not problems, problems


def validate_expected_corpus(
    cases: list[dict[str, Any]],
    database: AirQualityDatabase,
) -> list[str]:
    problems: list[str] = []
    identifiers = [case["id"] for case in cases]
    if len(identifiers) != len(set(identifiers)):
        problems.append("case IDs are not unique")

    for case in cases:
        if not case["turns"]:
            problems.append(f"{case['id']} has no turns")
            continue
        for turn_index, expected_turn in enumerate(case["turns"], start=1):
            if expected_turn["outcome"] not in {"execute", "abstain", "clarify"}:
                problems.append(
                    f"{case['id']} turn {turn_index} has an invalid outcome"
                )
            for expected_call in expected_turn["calls"]:
                name = expected_call["name"]
                if name not in TOOL_BY_NAME:
                    problems.append(
                        f"{case['id']} turn {turn_index} references unknown {name}"
                    )
                    continue
                try:
                    validation_arguments = expected_call.get("arguments", {})
                    if name == "clarify_question" and not validation_arguments:
                        validation_arguments = {
                            "question": "Which pollutant or scope should I use?"
                        }
                    analysis = build_analysis(
                        name,
                        validation_arguments,
                    )
                    if analysis.plan.in_scope:
                        _, rows, _ = database.execute(analysis.plan.sql)
                        if expected_turn["require_nonempty"] and not rows:
                            problems.append(
                                f"{case['id']} turn {turn_index} expected "
                                f"non-empty {name}"
                            )
                except Exception as exc:
                    problems.append(
                        f"{case['id']} turn {turn_index} invalid {name}: {exc}"
                    )
    return problems


def history_entry(
    question: str,
    routed: RoutedAnalysis,
    execution_notes: list[str],
) -> list[ChatMessage]:
    call_summary = json.dumps(
        [
            {"name": call.name, "arguments": call.arguments}
            for call in routed.calls
        ],
        ensure_ascii=False,
    )
    assistant = (
        "Verified previous analysis. Function calls: "
        f"{call_summary}. {' '.join(execution_notes)}"
    )
    return [
        ChatMessage(role="user", content=question),
        ChatMessage(role="assistant", content=assistant[:8_000]),
    ]


async def evaluate_case(
    *,
    case: dict[str, Any],
    repetition: int,
    pool: KeyPool,
    model: str,
    database: AirQualityDatabase,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        # One service per case, so the turn budget covers this conversation
        # rather than the whole batch.
        service = GeminiService(
            model=model,
            pool=pool,
            turn_budget_seconds=60.0 * len(case["turns"]),
        )
        history: list[ChatMessage] = []
        turn_reports: list[dict[str, Any]] = []
        case_passed = True

        for turn_index, expected_turn in enumerate(case["turns"], start=1):
            question = expected_turn["question"]
            started = time.perf_counter()
            repaired = False
            problems: list[str] = []
            execution_notes: list[str] = []
            routed: RoutedAnalysis | None = None
            try:
                routed = await asyncio.wait_for(
                    service.route(question, history),
                    timeout=service.call_timeout(25),
                )
                alignment_problem = routing_alignment_issue(
                    question,
                    routed.calls,
                    history,
                )
                for _ in range(2):
                    if not alignment_problem:
                        break
                    repaired = True
                    routed = await asyncio.wait_for(
                        service.route(
                            question,
                            history,
                            validation_feedback=(
                                f"{alignment_problem} The rejected plan called: "
                                + ", ".join(call.name for call in routed.calls)
                                + ". Rebuild the complete plan for every "
                                "requested clause, retaining any call that was "
                                "already correct."
                            ),
                        ),
                        timeout=service.call_timeout(25),
                    )
                    alignment_problem = routing_alignment_issue(
                        question,
                        routed.calls,
                        history,
                    )
                if alignment_problem:
                    problems.append(
                        f"alignment failed after repair: {alignment_problem}"
                    )

                matched, matching_problems = match_calls(
                    expected_turn["calls"],
                    routed,
                    expected_turn["outcome"],
                )
                if not matched:
                    problems.extend(matching_problems)

                for actual_call in routed.calls:
                    analysis = build_analysis(
                        actual_call.name,
                        actual_call.arguments,
                    )
                    if analysis.plan.in_scope:
                        _, rows, _ = database.execute(analysis.plan.sql)
                        execution_notes.append(
                            f"{actual_call.name} returned {len(rows)} rows."
                        )
                        if expected_turn["require_nonempty"] and not rows:
                            problems.append(
                                f"{actual_call.name} executed but returned no rows"
                            )
                    else:
                        execution_notes.append(
                            f"{actual_call.name} stopped before execution."
                        )
            except Exception as exc:
                problems.append(f"{type(exc).__name__}: {exc}")

            elapsed_ms = round((time.perf_counter() - started) * 1_000)
            passed = not problems
            case_passed = case_passed and passed
            actual_calls = (
                [
                    {"name": call.name, "arguments": call.arguments}
                    for call in routed.calls
                ]
                if routed
                else []
            )
            turn_reports.append(
                {
                    "turn": turn_index,
                    "question": question,
                    "expected_outcome": expected_turn["outcome"],
                    "expected_calls": expected_turn["calls"],
                    "actual_calls": actual_calls,
                    "repaired": repaired,
                    "elapsed_ms": elapsed_ms,
                    "passed": passed,
                    "problems": problems,
                }
            )
            if routed:
                history.extend(
                    history_entry(question, routed, execution_notes)
                )

        return {
            "id": case["id"],
            "category": case["category"],
            "repetition": repetition,
            "passed": case_passed,
            "turns": turn_reports,
            "note": case.get("note", ""),
        }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# VayuChat practical-question evaluation",
        "",
        f"- Model: `{report['model']}`",
        f"- Cases × repetitions: {report['case_count']} × {report['repetitions']}",
        f"- Evaluated turns: {report['turn_count']}",
        f"- Passing turns: {report['passing_turns']} "
        f"({report['turn_accuracy_pct']:.1f}%)",
        f"- Passing case-runs: {report['passing_case_runs']} / "
        f"{report['case_runs']}",
        f"- Median planner time: {report['median_latency_ms']:.0f} ms",
        f"- Repairs invoked: {report['repair_count']}",
        "",
        "## Accuracy by category",
        "",
        "| Category | Passed | Total | Accuracy |",
        "|---|---:|---:|---:|",
    ]
    for category, values in sorted(report["by_category"].items()):
        accuracy = 100 * values["passed"] / values["total"]
        lines.append(
            f"| {category} | {values['passed']} | {values['total']} | "
            f"{accuracy:.1f}% |"
        )

    failures = [
        (case_run, turn)
        for case_run in report["results"]
        for turn in case_run["turns"]
        if not turn["passed"]
    ]
    lines.extend(["", f"## Failures ({len(failures)})", ""])
    if not failures:
        lines.append("None.")
    else:
        for case_run, turn in failures:
            lines.extend(
                [
                    f"### {case_run['id']} · run {case_run['repetition']} "
                    f"· turn {turn['turn']}",
                    "",
                    f"**Question:** {turn['question']}",
                    "",
                    f"**Expected:** `{json.dumps(turn['expected_calls'], ensure_ascii=False)}`",
                    "",
                    f"**Actual:** `{json.dumps(turn['actual_calls'], ensure_ascii=False)}`",
                    "",
                    "**Problems:** " + "; ".join(turn["problems"]),
                    "",
                ]
            )
    return "\n".join(lines) + "\n"


async def run_live(args: argparse.Namespace, cases: list[dict[str, Any]]) -> dict[str, Any]:
    api_key = (
        os.getenv("GEMINI_API_KEYS", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    )
    if not api_key:
        raise RuntimeError("GEMINI_API_KEYS is required for --live")

    database = AirQualityDatabase()
    database.initialize()
    app_database.initialize()
    corpus_problems = validate_expected_corpus(cases, database)
    if corpus_problems:
        raise RuntimeError(
            "Expected corpus is invalid:\n- " + "\n- ".join(corpus_problems)
        )

    pool = KeyPool(parse_api_keys(api_key))
    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [
        evaluate_case(
            case=case,
            repetition=repetition,
            pool=pool,
            model=args.model,
            database=database,
            semaphore=semaphore,
        )
        for repetition in range(1, args.repetitions + 1)
        for case in cases
    ]
    results = await asyncio.gather(*tasks)

    turns = [turn for result in results for turn in result["turns"]]
    latencies = [turn["elapsed_ms"] for turn in turns]
    by_category: dict[str, Counter] = {}
    for result in results:
        counter = by_category.setdefault(result["category"], Counter())
        for evaluated_turn in result["turns"]:
            counter["total"] += 1
            counter["passed"] += int(evaluated_turn["passed"])

    return {
        "model": args.model,
        "case_count": len(cases),
        "repetitions": args.repetitions,
        "case_runs": len(results),
        "passing_case_runs": sum(result["passed"] for result in results),
        "turn_count": len(turns),
        "passing_turns": sum(turn["passed"] for turn in turns),
        "turn_accuracy_pct": 100
        * sum(turn["passed"] for turn in turns)
        / len(turns),
        "median_latency_ms": statistics.median(latencies),
        "repair_count": sum(turn["repaired"] for turn in turns),
        "by_category": {
            category: dict(counter)
            for category, counter in by_category.items()
        },
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or run the VayuChat practical-question suite."
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--model",
        default=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/evals/practical"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = PRACTICAL_CASES
    if args.category:
        allowed = set(args.category)
        cases = [case for case in cases if case["category"] in allowed]
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        raise SystemExit("No cases matched the selected filters.")

    database = AirQualityDatabase()
    database.initialize()
    corpus_problems = validate_expected_corpus(cases, database)
    if corpus_problems:
        raise SystemExit(
            "Expected corpus is invalid:\n- " + "\n- ".join(corpus_problems)
        )
    print(
        f"Validated {len(cases)} cases / "
        f"{sum(len(case['turns']) for case in cases)} turns offline."
    )
    if not args.live:
        return

    report = asyncio.run(run_live(args, cases))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "report.json"
    markdown_path = args.output_dir / "report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    print(markdown_report(report).split("## Failures", maxsplit=1)[0].strip())
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")


if __name__ == "__main__":
    main()
