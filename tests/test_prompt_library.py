from pathlib import Path
import re


PROMPT_SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "prompts.ts"
)


def built_in_prompts() -> list[str]:
    source = PROMPT_SOURCE.read_text(encoding="utf-8")
    return re.findall(r'^\s+"([^"]+\?)",?$', source, flags=re.MULTILINE)


def test_change_prompts_name_two_endpoint_years():
    incomplete = []
    for prompt in built_in_prompts():
        if not re.search(
            r"\b(?:change|changed|increase|improve|improved|reduced)\b",
            prompt,
            flags=re.IGNORECASE,
        ):
            continue
        years = re.findall(r"\b(?:19|20)\d{2}\b", prompt)
        if len(set(years)) < 2:
            incomplete.append(prompt)

    assert incomplete == [], (
        "Built-in change questions must include two endpoint years so clicking "
        f"them can execute directly: {incomplete}"
    )


def test_station_ranking_is_a_built_in_release_question():
    assert (
        "Which station had the highest average PM2.5 in Delhi during 2023?"
        in built_in_prompts()
    )
