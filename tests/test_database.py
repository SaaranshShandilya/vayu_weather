from backend.database import AirQualityDatabase


def test_real_dataset_loads_and_answers_a_city_ranking():
    database = AirQualityDatabase()
    database.initialize()

    stats = database.stats()
    assert stats["ready"] is True
    assert stats["records"] > 100_000
    assert stats["cities"] > 100

    columns, rows, truncated = database.execute(
        """
        SELECT city, ROUND(AVG(pm25), 2) AS avg_pm25
        FROM air_quality
        WHERE year = 2023 AND pm25 IS NOT NULL
        GROUP BY city
        ORDER BY avg_pm25 DESC
        LIMIT 10
        """
    )
    assert columns == ["city", "avg_pm25"]
    assert len(rows) == 10
    assert isinstance(rows[0]["avg_pm25"], float)
    assert truncated is False
