from backend.database import AirQualityDatabase
from evals.practical_cases import PRACTICAL_CASES
from scripts.run_practical_evals import validate_expected_corpus


def test_practical_eval_corpus_is_large_and_executable():
    database = AirQualityDatabase()
    database.initialize()

    assert len(PRACTICAL_CASES) >= 90
    assert sum(len(case["turns"]) for case in PRACTICAL_CASES) >= 98
    assert validate_expected_corpus(PRACTICAL_CASES, database) == []


def test_practical_eval_corpus_covers_release_risks():
    categories = {case["category"] for case in PRACTICAL_CASES}
    assert {
        "compound",
        "follow_up",
        "context_reset",
        "clarification",
        "security",
        "unsupported",
        "relationships",
        "threshold_frequency",
        "maps",
        "funding",
    } <= categories
