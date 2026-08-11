# GÉNÉRÉ depuis poc/famat/famat_recipes.py — ne pas éditer ici
"""Recettes analytiques FAMAT — POC dérive process.

Module autonome. Seules dépendances : duckdb, matplotlib, requests (stdlib :
statistics, tempfile, os).
Cuit dans l'image sandbox custom ET utilisé hors ligne pour les tests.
"""
import os
import statistics

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

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


def load_url(url: str, timeout: int = 60) -> duckdb.DuckDBPyConnection:
    """CSV webhook servi par HTTP (MinIO local en POC) -> table `events`."""
    import os
    import tempfile

    import requests

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(resp.content)
        return load_csv(path)
    finally:
        os.unlink(path)


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


def backtest(con, cle: str, chapter: int, k_sigma: float = 3.0, horizon: int = 5,
             min_train: int = 10, window: int = 10, jump_k: float = 5.0) -> dict:
    """Rejeu chronologique : à chaque pièce, limites et régression sur le passé seul.

    La pente est estimée sur les `window` derniers points du segment courant :
    c'est le taux de dérive ACTUEL — une régression sur tout le segment noierait
    une dérive récente dans l'historique stable et n'alerterait jamais à temps.
    """
    s = series(con, cle, chapter, jump_k)
    values = [p["value"] for p in s]
    segs = [p["segment_id"] for p in s]
    n = len(s)
    alerts = []
    for i in range(min_train, n):
        past = values[: i + 1]
        mean = statistics.fmean(past)
        sigma = statistics.stdev(past) if len(past) > 1 else 0.0
        if sigma == 0:
            continue
        lcl, ucl = mean - k_sigma * sigma, mean + k_sigma * sigma
        if not (lcl <= values[i] <= ucl):
            continue  # déjà dehors : trop tard pour "anticiper"
        seg_pts = [(s[j]["part_index"], values[j]) for j in range(i + 1) if segs[j] == segs[i]]
        seg_pts = seg_pts[-window:]
        if len(seg_pts) < 3:
            continue
        xs = [p[0] for p in seg_pts]
        ys = [p[1] for p in seg_pts]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den == 0:
            continue
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        predicted = values[i] + slope * horizon
        if predicted > ucl or predicted < lcl:
            alerts.append({"part_index": s[i]["part_index"], "predicted_crossing_at": predicted})

    # Franchissements réels (limites finales, calculées sur toute la série)
    mean_all = statistics.fmean(values)
    sigma_all = statistics.stdev(values) if n > 1 else 0.0
    lcl_all, ucl_all = mean_all - k_sigma * sigma_all, mean_all + k_sigma * sigma_all
    crossings = [s[i]["part_index"] for i in range(n)
                 if sigma_all > 0 and not (lcl_all <= values[i] <= ucl_all)]

    true_alerts, false_alerts, lead_times = 0, 0, []
    matched = set()
    for a in alerts:
        hit = next((c for c in crossings
                    if a["part_index"] < c <= a["part_index"] + horizon and c not in matched), None)
        if hit is not None:
            true_alerts += 1
            matched.add(hit)
            lead_times.append(hit - a["part_index"])
        else:
            false_alerts += 1
    return {"cle": cle, "chapter": chapter, "n_parts": n, "alerts": alerts,
            "crossings": crossings, "true_alerts": true_alerts,
            "false_alerts": false_alerts, "lead_times": lead_times,
            "missed_crossings": len([c for c in crossings if c not in matched])}


def temperature_restarts(con) -> dict:
    """Redémarrages (chapitre décroissant) et température pièce associée."""
    row = con.execute(
        """
        WITH chap AS (
            SELECT serial, seq, ts, chapter,
                   LAG(chapter) OVER (PARTITION BY serial ORDER BY seq) AS prev_chapter
            FROM events WHERE chapter IS NOT NULL
        ),
        restarts AS (
            SELECT serial, seq, ts FROM chap
            WHERE prev_chapter IS NOT NULL AND chapter < prev_chapter
        ),
        temp_at AS (
            SELECT r.serial, r.seq,
                   (SELECT e.value_num FROM events e
                    WHERE e.cle = 'TEMP_PIECE' AND e.serial = r.serial AND e.seq < r.seq
                    ORDER BY e.seq DESC LIMIT 1) AS temp
            FROM restarts r
        ),
        per_serial AS (
            SELECT e.serial,
                   (SELECT count(*) FROM restarts r WHERE r.serial = e.serial)  AS restarts,
                   avg(CASE WHEN e.cle = 'TEMP_PIECE' THEN e.value_num END)     AS mean_temp
            FROM events e GROUP BY e.serial
        )
        SELECT
            (SELECT count(*) FROM restarts),
            (SELECT count(DISTINCT serial) FROM restarts),
            (SELECT avg(temp) FROM temp_at),
            (SELECT avg(value_num) FROM events WHERE cle = 'TEMP_PIECE'),
            (SELECT corr(restarts, mean_temp) FROM per_serial WHERE mean_temp IS NOT NULL)
        """
    ).fetchone()
    per_serial = [
        {"serial": r[0], "restarts": int(r[1]), "mean_temp": r[2]}
        for r in con.execute(
            """
            WITH chap AS (
                SELECT serial, seq,
                       LAG(chapter) OVER (PARTITION BY serial ORDER BY seq) AS prev_chapter,
                       chapter
                FROM events WHERE chapter IS NOT NULL
            )
            SELECT e.serial,
                   count(CASE WHEN c.prev_chapter IS NOT NULL AND c.chapter < c.prev_chapter THEN 1 END),
                   avg(CASE WHEN e.cle = 'TEMP_PIECE' THEN e.value_num END)
            FROM events e
            LEFT JOIN chap c ON c.serial = e.serial AND c.seq = e.seq
            GROUP BY e.serial ORDER BY e.serial
            """
        ).fetchall()
    ]
    return {"n_restarts": int(row[0]), "n_serials_with_restart": int(row[1]),
            "mean_temp_at_restart": row[2], "mean_temp_overall": row[3],
            "corr_restarts_temp": row[4], "per_serial": per_serial}


def spc_chart(drift_result: dict, out_dir: str = "artifacts", fmt: str = "svg") -> str:
    """Carte de contrôle SPC depuis un résultat de drift()."""
    d = drift_result
    s = d["series"]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"spc_{d['cle']}_ch{d['chapter']}.{fmt}")

    xs = [p["part_index"] for p in s]
    ys = [p["value"] for p in s]
    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=200)
    ax.axhspan(d["lcl"], d["ucl"], color="#2b8a3e", alpha=0.07)
    ax.axhline(d["mean"], color="#2b8a3e", lw=1, label="moyenne")
    ax.axhline(d["ucl"], color="#e03131", lw=1, ls="--", label=f"±{3:.0f}σ")
    ax.axhline(d["lcl"], color="#e03131", lw=1, ls="--")
    inside = [(x, y) for x, y in zip(xs, ys) if d["lcl"] <= y <= d["ucl"]]
    outside = [(x, y) for x, y in zip(xs, ys) if not (d["lcl"] <= y <= d["ucl"])]
    if inside:
        ax.plot(*zip(*inside), "o-", ms=3.5, lw=0.8, color="#1971c2")
    if outside:
        ax.plot(*zip(*outside), "o", ms=5, color="#e03131", label="hors limites")

    seg_starts = {}
    for p in s:
        seg_starts.setdefault(p["segment_id"], p["part_index"])
    for seg in d["segments"]:
        pts = [(p["part_index"], p["value"]) for p in s if p["segment_id"] == seg["segment_id"]]
        if len(pts) >= 2 and seg["slope_per_part"] is not None:
            x0, x1 = pts[0][0], pts[-1][0]
            y0 = seg["intercept"] + seg["slope_per_part"] * x0
            y1 = seg["intercept"] + seg["slope_per_part"] * x1
            ax.plot([x0, x1], [y0, y1], color="#f08c00", lw=1.6)
            ax.annotate(f"{seg['slope_per_part'] * 1000:+.2f} µm/pièce",
                        xy=(x1, y1), fontsize=8, color="#f08c00",
                        xytext=(4, 4), textcoords="offset points")
        if seg_starts[seg["segment_id"]] > 1:
            ax.axvline(seg_starts[seg["segment_id"]] - 0.5, color="#868e96", lw=0.8, ls=":")

    ax.set_title(f"Carte de contrôle — {d['cle']} (chapitre {d['chapter']}), {d['n_parts']} pièces")
    ax.set_xlabel("pièce (ordre chronologique)")
    ax.set_ylabel("valeur (mm)")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, format=fmt)
    plt.close(fig)
    return path
