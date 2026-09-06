"""TDD — recettes santé (Apple Health export.xml → table DuckDB `data`).

Le socle qui porte la JUSTESSE de l'analyse : des agrégations testées, pas
des stats improvisées par le LLM. Le LLM se contente de choisir la recette
et ses paramètres, puis d'interpréter le résultat.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import health_recipes as hr


def _con():
    # type, value, unit, startDate (format Apple : 'YYYY-MM-DD HH:MM:SS +ZZZZ')
    rows = [
        ("HKQuantityTypeIdentifierStepCount", "500", "count", "2026-08-01 08:00:00 +0200"),
        ("HKQuantityTypeIdentifierStepCount", "500", "count", "2026-08-01 09:00:00 +0200"),
        ("HKQuantityTypeIdentifierStepCount", "3000", "count", "2026-08-02 08:00:00 +0200"),
        ("HKQuantityTypeIdentifierStepCount", "100", "count", "2026-07-31 08:00:00 +0200"),
    ]
    return hr.load_rows(rows)


def test_daily_summary_averages_over_period():
    r = hr.daily_summary(_con(), "HKQuantityTypeIdentifierStepCount", period="2026-08")
    assert r["days"] == 2
    assert r["total"] == 4000
    assert r["avg_per_day"] == 2000.0


def _con_trend():
    rows = []
    for day, steps in [("01", 1000), ("02", 2000), ("03", 3000), ("04", 4000)]:
        rows.append(("HKQuantityTypeIdentifierStepCount", str(steps), "count",
                     f"2026-08-{day} 08:00:00 +0200"))
    return hr.load_rows(rows)


def test_trend_detects_upward_slope():
    r = hr.trend(_con_trend(), "HKQuantityTypeIdentifierStepCount", period="2026-08")
    assert r["points"] == 4
    assert r["first"] == 1000.0
    assert r["last"] == 4000.0
    assert r["slope_per_day"] == 1000.0
    assert r["direction"] == "up"


def test_list_metrics_reports_types_counts_and_range():
    rows = [
        ("HKQuantityTypeIdentifierStepCount", "500", "count", "2026-08-01 08:00:00 +0200"),
        ("HKQuantityTypeIdentifierStepCount", "500", "count", "2026-08-03 09:00:00 +0200"),
        ("HKQuantityTypeIdentifierDistanceWalkingRunning", "2.1", "km", "2026-08-02 08:00:00 +0200"),
    ]
    r = hr.list_metrics(hr.load_rows(rows))
    by = {m["type"]: m for m in r}
    assert by["HKQuantityTypeIdentifierStepCount"]["count"] == 2
    assert by["HKQuantityTypeIdentifierStepCount"]["first"] == "2026-08-01"
    assert by["HKQuantityTypeIdentifierStepCount"]["last"] == "2026-08-03"
    assert by["HKQuantityTypeIdentifierDistanceWalkingRunning"]["unit"] == "km"
