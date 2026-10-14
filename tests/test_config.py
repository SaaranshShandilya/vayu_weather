from pathlib import Path

from backend.config import get_settings, load_env_file


POOL_VARIABLES = (
    "GEMINI_API_KEYS",
    "GEMINI_API_KEY",
    "GEMINI_KEY_RPM",
    "GEMINI_RPM_PER_KEY",
    "GEMINI_KEY_RPD",
    "GEMINI_RPD_PER_KEY",
    "GEMINI_KEY_WAIT_SECONDS",
    "GEMINI_KEY_WAIT_S",
    "GEMINI_TURN_BUDGET_SECONDS",
    "GEMINI_TURN_BUDGET_S",
    "API_CONCURRENCY_LIMIT",
    "API_THREAD_LIMIT",
)


def clear_pool_env(monkeypatch) -> None:
    for name in POOL_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_env_file_is_loaded_without_overriding_real_variables(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "GEMINI_API_KEYS=one,two",
                'APP_PASSWORD="quoted value"',
                "export GEMINI_MODEL=from-file",
                "NOT_AN_ASSIGNMENT",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "from-the-environment")

    load_env_file(env_file)

    import os

    assert os.environ["GEMINI_API_KEYS"] == "one,two"
    assert os.environ["APP_PASSWORD"] == "quoted value"
    # A real environment variable always wins over the file.
    assert os.environ["GEMINI_MODEL"] == "from-the-environment"
    assert "NOT_AN_ASSIGNMENT" not in os.environ


def test_a_missing_env_file_is_not_an_error():
    load_env_file(Path("no-such-directory") / ".env")


def test_pool_settings_accept_the_deployment_variable_names(monkeypatch):
    clear_pool_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEYS", "key-one, key-two")
    monkeypatch.setenv("GEMINI_RPM_PER_KEY", "20")
    monkeypatch.setenv("GEMINI_KEY_WAIT_S", "40")
    monkeypatch.setenv("API_THREAD_LIMIT", "96")

    settings = get_settings()

    assert settings.gemini_api_keys == ("key-one", "key-two")
    assert settings.key_rpm == 20
    assert settings.key_wait_seconds == 40.0
    assert settings.api_concurrency_limit == 96


def test_explicit_names_win_over_the_aliases(monkeypatch):
    clear_pool_env(monkeypatch)
    monkeypatch.setenv("GEMINI_KEY_RPM", "5")
    monkeypatch.setenv("GEMINI_RPM_PER_KEY", "20")
    monkeypatch.setenv("GEMINI_KEY_WAIT_SECONDS", "10")
    monkeypatch.setenv("GEMINI_KEY_WAIT_S", "40")

    settings = get_settings()

    assert settings.key_rpm == 5
    assert settings.key_wait_seconds == 10.0


def test_turn_budget_leaves_room_for_a_full_lease_wait(monkeypatch):
    clear_pool_env(monkeypatch)
    monkeypatch.setenv("GEMINI_KEY_WAIT_SECONDS", "40")
    assert get_settings().turn_budget_seconds == 60.0

    monkeypatch.setenv("GEMINI_KEY_WAIT_SECONDS", "5")
    assert get_settings().turn_budget_seconds == 45.0

    monkeypatch.setenv("GEMINI_TURN_BUDGET_SECONDS", "12")
    assert get_settings().turn_budget_seconds == 12.0


def test_pool_defaults_are_unpaced(monkeypatch):
    clear_pool_env(monkeypatch)
    settings = get_settings()
    assert settings.key_rpm == 0
    assert settings.key_rpd == 0
    assert settings.api_concurrency_limit == 0
    assert settings.gemini_api_keys == ()
    assert not settings.is_configured


def test_a_malformed_number_falls_back_to_the_default(monkeypatch):
    clear_pool_env(monkeypatch)
    monkeypatch.setenv("GEMINI_KEY_RPM", "twenty")
    monkeypatch.setenv("GEMINI_KEY_WAIT_SECONDS", "soon")
    settings = get_settings()
    assert settings.key_rpm == 0
    assert settings.key_wait_seconds == 25.0
