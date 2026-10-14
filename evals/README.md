# VayuChat practical evaluations

This directory separates **question discovery** from **release ground truth**.

- `generated/gemini_practical_candidates_2026-07-25.json` contains 180
  independent Gemini-generated candidate questions. Its proposed tools are
  deliberately marked unreviewed and are never used as release truth.
- `practical_cases.py` contains the reviewed suite: exact expected typed
  functions, argument subsets, compound calls, follow-up sequences,
  clarification, abstention, and security cases.

Validate every reviewed plan and execute it against the bundled tables without
calling Gemini:

```bash
.venv/bin/python scripts/run_practical_evals.py
```

Run the current Gemini router and score every turn:

```bash
.venv/bin/python scripts/run_practical_evals.py --live
```

For stability testing, repeat every case three times:

```bash
.venv/bin/python scripts/run_practical_evals.py --live --repetitions 3
```

Reports are written to `outputs/evals/practical/report.json` and `report.md`.
The runner records exact expected/actual calls, repaired plans, execution
emptiness, per-turn latency, category accuracy, and conversation history.
