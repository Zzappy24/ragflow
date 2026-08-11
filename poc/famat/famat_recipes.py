"""Recettes analytiques FAMAT — POC dérive process.

Module autonome : seules dépendances duckdb, matplotlib, pymysql.
Cuit dans l'image sandbox custom ET utilisé hors ligne pour les tests.
"""
import duckdb

_EVENTS_SCHEMA = "seq BIGINT, serial VARCHAR, chapter INTEGER, cle VARCHAR, value_num DOUBLE, ts TIMESTAMP"


def load_csv(path: str) -> duckdb.DuckDBPyConnection:
    """CSV webhook brut (séparateur ';', BOM utf-8) -> table `events`."""
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE events AS
        SELECT
            webhookmesure_id AS seq,
            json_extract_string(webhookmesure_contenu, '$.Serial')  AS serial,
            TRY_CAST(json_extract_string(webhookmesure_contenu, '$.Chapter') AS INTEGER) AS chapter,
            json_extract_string(webhookmesure_contenu, '$.cle')     AS cle,
            TRY_CAST(json_extract_string(webhookmesure_contenu, '$.value') AS DOUBLE)   AS value_num,
            TRY_CAST(webhookmesure_datecreation AS TIMESTAMP)       AS ts
        FROM read_csv(?, delim=';', header=true, normalize_names=true)
        """,
        [path],
    )
    return con


def load_rows(rows) -> duckdb.DuckDBPyConnection:
    """Tuples (seq, serial, chapter, cle, value_num, ts) -> table `events`."""
    con = duckdb.connect()
    con.execute(f"CREATE TABLE events ({_EVENTS_SCHEMA})")
    con.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", rows)
    return con


def series(con, cle: str, chapter: int, jump_k: float = 5.0) -> list[dict]:
    """Série chronologique par pièce pour (cle, chapter), segmentée par ruptures.

    Une pièce = un point (dernière mesure retenue). Une rupture = |diff|
    dépassant jump_k fois la médiane des |diff| non nuls (re-réglage machine).
    """
    rows = con.execute(
        """
        WITH per_part AS (
            SELECT serial,
                   arg_max(value_num, seq) AS value,
                   max(ts)                 AS ts
            FROM events
            WHERE cle = ? AND chapter = ? AND value_num IS NOT NULL
            GROUP BY serial
        ),
        ordered AS (
            SELECT serial, value, ts,
                   ROW_NUMBER() OVER (ORDER BY ts)          AS part_index,
                   value - LAG(value) OVER (ORDER BY ts)    AS diff
            FROM per_part
        ),
        med AS (
            SELECT COALESCE(median(abs(diff)), 0) AS mad FROM ordered WHERE diff IS NOT NULL AND diff <> 0
        )
        SELECT o.serial, o.value, CAST(o.ts AS VARCHAR) AS ts, o.part_index,
               SUM(CASE WHEN o.diff IS NOT NULL AND m.mad > 0
                         AND abs(o.diff) > ? * m.mad THEN 1 ELSE 0 END)
                   OVER (ORDER BY o.part_index ROWS UNBOUNDED PRECEDING) AS segment_id
        FROM ordered o CROSS JOIN med m
        ORDER BY o.part_index
        """,
        [cle, chapter, jump_k],
    ).fetchall()
    return [
        {"serial": r[0], "value": r[1], "ts": r[2], "part_index": r[3], "segment_id": int(r[4])}
        for r in rows
    ]


def drift(con, cle: str, chapter: int, k_sigma: float = 3.0, jump_k: float = 5.0) -> dict:
    """Analyse de dérive : régression par segment + limites ±k_sigma + extrapolation."""
    s = series(con, cle, chapter, jump_k)
    if not s:
        return {"cle": cle, "chapter": chapter, "n_parts": 0, "series": [],
                "segments": [], "current": None, "mean": None, "sigma": None,
                "lcl": None, "ucl": None}

    seg_con = duckdb.connect()  # connexion de travail vide
    seg_con.execute("CREATE TABLE pts (part_index INTEGER, value DOUBLE, segment_id INTEGER)")
    seg_con.executemany(
        "INSERT INTO pts VALUES (?, ?, ?)",
        [(p["part_index"], p["value"], p["segment_id"]) for p in s],
    )
    mean, sigma = seg_con.execute("SELECT avg(value), stddev_samp(value) FROM pts").fetchone()
    sigma = sigma or 0.0
    lcl, ucl = mean - k_sigma * sigma, mean + k_sigma * sigma

    segments = [
        {"segment_id": int(r[0]), "n": int(r[1]),
         "slope_per_part": r[2], "intercept": r[3]}
        for r in seg_con.execute(
            """
            SELECT segment_id, count(*),
                   regr_slope(value, part_index),
                   regr_intercept(value, part_index)
            FROM pts GROUP BY segment_id ORDER BY segment_id
            """
        ).fetchall()
    ]

    last = s[-1]
    cur_seg = next(x for x in segments if x["segment_id"] == last["segment_id"])
    slope = cur_seg["slope_per_part"]
    parts_to_limit = None
    if slope is not None and sigma > 0 and abs(slope) > 1e-12:
        target = ucl if slope > 0 else lcl
        remaining = (target - last["value"]) / slope
        if remaining > 0:
            parts_to_limit = remaining
    current = {
        "segment_id": cur_seg["segment_id"],
        "slope_per_part": slope,
        "last_value": last["value"],
        "last_part_index": last["part_index"],
        "parts_to_limit": parts_to_limit,
        "beyond_limits": not (lcl <= last["value"] <= ucl),
    }
    return {"cle": cle, "chapter": chapter, "n_parts": len(s), "mean": mean,
            "sigma": sigma, "lcl": lcl, "ucl": ucl, "series": s,
            "segments": segments, "current": current}
