# GÉNÉRÉ depuis poc/sante/health_recipes.py — ne pas éditer ici
"""Recettes santé — Apple Health export.xml → DuckDB, table `data`.

Module autonome (dépendance : duckdb). Les colonnes de `data` sont les
attributs des <Record> de l'export : type, value, unit, startDate, ...
(toutes en TEXTE). Les recettes castent et agrègent — la JUSTESSE de
l'analyse vit ici, testée, pas dans du code improvisé par le LLM.

Format de date Apple : 'YYYY-MM-DD HH:MM:SS +ZZZZ'.
"""
import duckdb

_DATA_SCHEMA = "type VARCHAR, value VARCHAR, unit VARCHAR, startDate VARCHAR"


def load_rows(rows) -> duckdb.DuckDBPyConnection:
    """Table `data` synthétique (tests) : (type, value, unit, startDate)."""
    con = duckdb.connect()
    con.execute(f"CREATE TABLE data ({_DATA_SCHEMA})")
    con.executemany("INSERT INTO data VALUES (?, ?, ?, ?)", rows)
    return con


def daily_summary(con, metric: str, period: str | None = None) -> dict:
    """Agrégation quotidienne d'une mesure sur une période.

    period : préfixe de date, ex '2026-08' (un mois) ou '2026' (une année)
    ou '2026-08-01' (un jour). None = toute la plage.
    Renvoie total, nb de jours actifs, moyenne/jour, min/max journaliers.
    """
    where = "type = ?"
    args = [metric]
    if period:
        where += " AND substr(startDate, 1, ?) = ?"
        args += [len(period), period]
    per_day = con.execute(
        f"""
        SELECT substr(startDate, 1, 10) AS day, SUM(CAST(value AS DOUBLE)) AS total
        FROM data WHERE {where}
        GROUP BY day ORDER BY day
        """,
        args,
    ).fetchall()
    if not per_day:
        return {"metric": metric, "period": period, "days": 0, "total": 0.0,
                "avg_per_day": 0.0, "min_day": None, "max_day": None}
    totals = [t for _, t in per_day]
    days = len(per_day)
    total = sum(totals)
    return {
        "metric": metric,
        "period": period,
        "days": days,
        "total": total,
        "avg_per_day": total / days,
        "min_day": min(totals),
        "max_day": max(totals),
    }


def trend(con, metric: str, period: str | None = None) -> dict:
    """Évolution quotidienne d'une mesure + pente (régression linéaire simple).

    Renvoie les totaux par jour, la première/dernière valeur, la pente en
    unités/jour (moindres carrés sur l'index du jour) et une direction
    lisible (up / down / flat).
    """
    where = "type = ?"
    args = [metric]
    if period:
        where += " AND substr(startDate, 1, ?) = ?"
        args += [len(period), period]
    per_day = con.execute(
        f"""
        SELECT substr(startDate, 1, 10) AS day, SUM(CAST(value AS DOUBLE)) AS total
        FROM data WHERE {where}
        GROUP BY day ORDER BY day
        """,
        args,
    ).fetchall()
    n = len(per_day)
    if n == 0:
        return {"metric": metric, "period": period, "points": 0, "first": None,
                "last": None, "slope_per_day": 0.0, "direction": "flat"}
    ys = [t for _, t in per_day]
    xs = list(range(n))
    if n == 1:
        slope = 0.0
    else:
        mx = sum(xs) / n
        my = sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else 0.0
    # seuil de platitude : 1 % de la moyenne par jour
    flat_eps = (sum(ys) / n) * 0.01
    direction = "flat" if abs(slope) <= flat_eps else ("up" if slope > 0 else "down")
    return {
        "metric": metric,
        "period": period,
        "points": n,
        "first": ys[0],
        "last": ys[-1],
        "slope_per_day": slope,
        "direction": direction,
        "per_day": [{"day": d, "total": t} for d, t in per_day],
    }


def list_metrics(con) -> list[dict]:
    """Inventaire des mesures présentes : type, unité, nombre d'enregistrements,
    bornes de dates. À appeler en premier pour découvrir les données réelles
    (les types Apple HealthKit varient d'un export à l'autre) — jamais deviner
    un nom de type.
    """
    rows = con.execute(
        """
        SELECT type,
               any_value(unit) AS unit,
               count(*) AS n,
               min(substr(startDate, 1, 10)) AS first_day,
               max(substr(startDate, 1, 10)) AS last_day
        FROM data
        GROUP BY type
        ORDER BY n DESC
        """
    ).fetchall()
    return [
        {"type": t, "unit": u, "count": n, "first": f, "last": last}
        for t, u, n, f, last in rows
    ]
