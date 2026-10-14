from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ALLOWED_OUTCOMES = {"execute", "abstain", "clarify"}
ALLOWED_VISUALS = {
    "number",
    "table",
    "bar",
    "line",
    "scatter",
    "map",
    "catalog",
    "text",
    "multiple",
    "none",
}


def parse_batch(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise ValueError(f"{path} did not contain a JSON array")
    return payload


def validate_case(case: dict) -> None:
    required = {
        "id",
        "category",
        "turns",
        "expected_outcome",
        "expected_tools",
        "must_preserve",
        "expected_visual",
        "rationale",
    }
    missing = required - case.keys()
    if missing:
        raise ValueError(f"{case.get('id', '<unknown>')} is missing {sorted(missing)}")
    if case["expected_outcome"] not in ALLOWED_OUTCOMES:
        raise ValueError(f"{case['id']} has an invalid outcome")
    if case["expected_visual"] not in ALLOWED_VISUALS:
        raise ValueError(f"{case['id']} has an invalid visual")
    if not 1 <= len(case["turns"]) <= 3:
        raise ValueError(f"{case['id']} must contain one to three turns")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize Gemini-generated VayuChat evaluation candidates."
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    cases: list[dict] = []
    for path in args.inputs:
        cases.extend(parse_batch(path))
    for case in cases:
        validate_case(case)

    identifiers = [case["id"] for case in cases]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Candidate IDs must be unique")

    document = {
        "metadata": {
            "generator": "Gemini 2.5 Flash via Gemini CLI",
            "generated_on": "2026-07-25",
            "review_status": (
                "Candidate questions only. Expected tools are model-proposed and "
                "must not be treated as ground truth until reviewed."
            ),
            "case_count": len(cases),
        },
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
