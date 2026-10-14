from backend.database import AirQualityDatabase
from backend.app import clean_model_text, render_result_summary
from backend.security import (
    create_session_token,
    session_cookie_policy,
    verify_session_token,
)
from starlette.requests import Request


def request_with_scheme(
    scheme: str = "http",
    forwarded_proto: str | None = None,
) -> Request:
    headers = []
    if forwarded_proto:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": scheme,
            "path": "/api/login",
            "raw_path": b"/api/login",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("localhost", 7860),
        }
    )


def test_session_token_round_trip():
    token = create_session_token("correct horse battery staple", 60)
    assert verify_session_token(token, "correct horse battery staple")
    assert not verify_session_token(token, "wrong password")


def test_expired_session_token_is_rejected():
    token = create_session_token("password", -1)
    assert not verify_session_token(token, "password")


def test_local_http_session_cookie_is_same_site_lax():
    assert session_cookie_policy(request_with_scheme()) == (False, "lax")


def test_https_session_cookie_supports_hugging_face_iframe():
    assert session_cookie_policy(request_with_scheme("https")) == (True, "none")
    assert session_cookie_policy(
        request_with_scheme(forwarded_proto="https")
    ) == (True, "none")


def test_sql_guard_accepts_read_only_queries():
    sql = AirQualityDatabase.validate_sql(
        """
        WITH city_avg AS (
            SELECT city, AVG(pm25) AS avg_pm25
            FROM air_quality
            WHERE pm25 IS NOT NULL
            GROUP BY city
        )
        SELECT city, ROUND(avg_pm25, 2) AS avg_pm25
        FROM city_avg
        ORDER BY avg_pm25 DESC
        LIMIT 10
        """
    )
    assert sql.lower().startswith("with")


def test_sql_guard_rejects_file_access_and_writes():
    blocked = [
        "SELECT * FROM read_csv_auto('/proc/self/environ')",
        "COPY air_quality TO '/tmp/export.csv'",
        "SELECT * FROM air_quality; DROP TABLE air_quality",
        "PRAGMA database_list",
        "SELECT * FROM air_quality, information_schema.tables",
        "SELECT * FROM duckdb_secrets()",
        "SELECT * FROM air_quality CROSS JOIN states",
        "SELECT * FROM air_quality, states",
        (
            "WITH RECURSIVE x(n) AS "
            "(SELECT 1 UNION ALL SELECT n + 1 FROM x) "
            "SELECT * FROM x, air_quality"
        ),
        "SELECT * FROM air_quality, range(1000000000)",
    ]
    for sql in blocked:
        try:
            AirQualityDatabase.validate_sql(sql)
        except ValueError:
            continue
        raise AssertionError(f"Unsafe SQL was accepted: {sql}")


def test_sql_guard_rejects_unknown_tables():
    try:
        AirQualityDatabase.validate_sql("SELECT * FROM information_schema.tables")
    except ValueError:
        return
    raise AssertionError("Unknown table was accepted")


def test_model_text_control_characters_are_removed():
    assert clean_model_text("151.51 \bµg/m\b³") == "151.51 µg/m³"


def test_model_text_latex_units_are_normalized():
    text = r"Delhi averaged 104.61 $\mu\text{g/m}^3$ and CO was 1.2 $\text{mg/m}^3$."
    assert clean_model_text(text) == (
        "Delhi averaged 104.61 µg/m³ and CO was 1.2 mg/m³."
    )


def test_model_text_common_plain_unit_variants_are_normalized():
    assert clean_model_text("140 Pcg/cm³; 80 μg/m³; 50 ug/m3") == (
        "140 µg/m³; 80 µg/m³; 50 µg/m³"
    )


def test_verified_result_values_fill_summary_template():
    answer = render_result_summary(
        (
            "{{city}} ranks highest at {{average_pm25}} µg/m³. "
            "The table contains {{result_count}} qualifying cities."
        ),
        ["city", "average_pm25", "observation_days"],
        [
            {
                "city": "Byrnihat",
                "average_pm25": 140.51,
                "observation_days": 1_200,
            },
            {
                "city": "Delhi",
                "average_pm25": 104.61,
                "observation_days": 2_000,
            },
        ],
    )
    assert answer == (
        "Byrnihat ranks highest at 140.51 µg/m³. "
        "The table contains 2 qualifying cities."
    )


def test_unknown_summary_placeholder_uses_safe_fallback():
    answer = render_result_summary(
        "The answer is {{invented_value}}.",
        ["city", "average_pm25"],
        [{"city": "Byrnihat", "average_pm25": 140.51}],
    )
    assert "{{invented_value}}" not in answer
    assert "Byrnihat" in answer
