# FAMAT Drift Agent POC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un agent conversationnel RAGFlow qui analyse les données d'usinage FAMAT (90 375 événements webhook) : dérive par clé/chapitre avec régression par segment, backtest d'alertes ±3σ, corrélation température↔redémarrages — cartes de contrôle SPC en pièces jointes, interprétation en français.

**Architecture:** Recettes analytiques déterministes dans un module `famat_recipes.py` (DuckDB + matplotlib) **cuit dans l'image sandbox custom** — le code exécuté par le tool `code_exec` se réduit à `import famat_recipes; famat_recipes.drift(...)`, et va chercher les données à la source (base SQL Cyllene) sans jamais faire transiter les 90k lignes par le contexte LLM. Le tool `execute_sql` (ExeSQL) sert uniquement à l'exploration libre (agrégats, échantillons). Composant Agent tool-calling par-dessus, prompt système = contexte métier + catalogue des recettes.

**Tech Stack:** DuckDB (regr_slope/regr_intercept, fenêtrage), matplotlib (Agg, SVG/PNG), pymysql, sandbox self_managed (executor-manager Docker), canvas RAGFlow (Agent + ExeSQL + CodeExec).

## Global Constraints

- **Aucune donnée client dans git** : `poc/famat/data/` est gitignoré ; le CSV FAMAT n'est jamais commité (souveraineté).
- **Ne pas toucher au MariaDB/MySQL du stack RAGFlow** : aucune donnée FAMAT n'y est chargée.
- **Providers sandbox cloud (e2b, aliyun) interdits** : `local` pour le dev, `self_managed` pour la démo.
- **duckdb n'est PAS ajouté à pyproject.toml** : utiliser `uv run --with duckdb` pour les scripts/tests hors sandbox.
- **Commits sans trailer `Co-Authored-By`** (règle du repo).
- Le dump source vit hors repo : `/Users/zappy/Library/Containers/com.microsoft.teams2/Data/tmp/Payload-20260526.csv` (copié en Task 1 vers `poc/famat/data/`).
- Format CSV source : séparateur `;`, BOM UTF-8, colonnes `WebhookMesure_Id`, `WebhookMesure_Contenu` (JSON `{"Serial","Chapter","cle","value"}`), `WebhookMesure_DateCreation`.
- Invariants du dump (validés par profilage, utilisés dans les tests) : 90 375 lignes, 94 serials, chapitres 0→57 (+99, + nulls), redémarrages sur 94/94 serials (moyenne ~5,4), `CORRECTION_X` non nulle sur ≥ 9 000 lignes, `TEMP_PIECE` ∈ [14, 26].

---

### Task 1: Squelette `poc/famat` + loader CSV DuckDB

**Files:**
- Create: `poc/famat/famat_recipes.py`
- Create: `poc/famat/data/.gitignore`
- Create: `poc/famat/README.md`
- Test: `poc/famat/tests/test_recipes.py`

**Interfaces:**
- Produces: `load_csv(path: str) -> duckdb.DuckDBPyConnection` — connexion in-memory avec table `events(seq BIGINT, serial VARCHAR, chapter INTEGER, cle VARCHAR, value_num DOUBLE, ts TIMESTAMP)`. `chapter`/`value_num` sont NULL si non parsables.
- Produces: `load_rows(rows: list[tuple]) -> duckdb.DuckDBPyConnection` — même table depuis des tuples `(seq, serial, chapter, cle, value_num, ts)` (tests synthétiques + chemin DB plus tard).

- [ ] **Step 1: Copier le dump hors-git et verrouiller le .gitignore**

```bash
mkdir -p poc/famat/data poc/famat/tests
cp "/Users/zappy/Library/Containers/com.microsoft.teams2/Data/tmp/Payload-20260526.csv" poc/famat/data/
printf '*\n!.gitignore\n' > poc/famat/data/.gitignore
```

- [ ] **Step 2: Écrire les tests qui échouent**

```python
# poc/famat/tests/test_recipes.py
import os
import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import famat_recipes as fr

CSV = os.environ.get("FAMAT_CSV", os.path.join(os.path.dirname(__file__), "..", "data", "Payload-20260526.csv"))
has_csv = os.path.exists(CSV)
real_data = pytest.mark.skipif(not has_csv, reason="dump FAMAT absent")


@real_data
def test_load_csv_row_count():
    con = fr.load_csv(CSV)
    assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 90375


@real_data
def test_load_csv_parses_json_fields():
    con = fr.load_csv(CSV)
    n_serials, n_keys = con.execute(
        "SELECT count(DISTINCT serial), count(DISTINCT cle) FROM events"
    ).fetchone()
    assert n_serials == 94
    assert n_keys == 109
    # CORRECTION_X massivement numérique et non nulle
    nz = con.execute(
        "SELECT count(*) FROM events WHERE cle='CORRECTION_X' AND value_num IS NOT NULL AND value_num <> 0"
    ).fetchone()[0]
    assert nz >= 9000


def test_load_rows_synthetic():
    rows = [
        (1, "S1", 2, "CORRECTION_X", 0.01, "2026-01-01 10:00:00"),
        (2, "S1", 3, "CORRECTION_X", 0.02, "2026-01-01 10:01:00"),
    ]
    con = fr.load_rows(rows)
    assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    assert con.execute("SELECT value_num FROM events WHERE seq=2").fetchone()[0] == pytest.approx(0.02)
```

- [ ] **Step 3: Vérifier l'échec**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v`
Expected: FAIL — `AttributeError: module 'famat_recipes' has no attribute 'load_csv'` (ou ImportError).

- [ ] **Step 4: Implémenter le loader**

```python
# poc/famat/famat_recipes.py
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
```

- [ ] **Step 5: Vérifier que les tests passent**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v`
Expected: 3 PASS.

- [ ] **Step 6: README minimal + commit**

```markdown
# POC FAMAT — détection de dérive process

Spec : docs/superpowers/specs/2026-08-11-famat-drift-agent-poc-design.md
Données : `data/Payload-20260526.csv` (JAMAIS commité — .gitignore).
Tests : `uv run --with duckdb python -m pytest poc/famat/tests/ -v`
```

```bash
git add poc/famat
git commit -m "feat(famat-poc): squelette recettes + loader CSV DuckDB"
```

---

### Task 2: Série par pièce + segmentation par ruptures

**Files:**
- Modify: `poc/famat/famat_recipes.py`
- Test: `poc/famat/tests/test_recipes.py`

**Interfaces:**
- Consumes: `load_csv` / `load_rows` (Task 1).
- Produces: `series(con, cle: str, chapter: int, jump_k: float = 5.0) -> list[dict]` — une entrée par pièce, chronologique : `{"part_index": int (1-based), "serial": str, "value": float, "ts": str, "segment_id": int (0-based)}`. Valeur retenue par pièce = **dernière** mesure (post-correction, état conservé). Nouveau segment quand `|diff| > jump_k × médiane(|diff|)` (ruptures = re-réglages).

- [ ] **Step 1: Tests qui échouent (synthétique à segments connus + invariants réels)**

```python
# ajouter à poc/famat/tests/test_recipes.py

def _mk(seq, serial, value, minute):
    return (seq, serial, 10, "CORRECTION_X", value, f"2026-01-01 10:{minute:02d}:00")


def test_series_orders_parts_and_takes_last_value():
    rows = [
        _mk(1, "A", 0.010, 0),
        _mk(2, "A", 0.011, 1),   # 2e mesure de A -> retenue
        _mk(3, "B", 0.012, 2),
    ]
    s = fr.series(fr.load_rows(rows), "CORRECTION_X", 10)
    assert [p["serial"] for p in s] == ["A", "B"]
    assert s[0]["value"] == pytest.approx(0.011)
    assert s[0]["part_index"] == 1 and s[1]["part_index"] == 2


def test_series_detects_segment_break_on_jump():
    # 10 pièces à pas +0.001, puis saut brutal (re-réglage), puis 10 pièces
    rows = [_mk(i + 1, f"P{i:02d}", 0.001 * i, i) for i in range(10)]
    rows += [_mk(i + 11, f"Q{i:02d}", -0.050 + 0.001 * i, i + 10) for i in range(10)]
    s = fr.series(fr.load_rows(rows), "CORRECTION_X", 10)
    assert len(s) == 20
    assert s[9]["segment_id"] == 0
    assert s[10]["segment_id"] == 1
    assert len({p["segment_id"] for p in s}) == 2


@real_data
def test_series_real_correction_x():
    con = fr.load_csv(CSV)
    s = fr.series(con, "CORRECTION_X", 10)
    assert 50 <= len(s) <= 94          # une entrée max par serial
    idx = [p["part_index"] for p in s]
    assert idx == sorted(idx) == list(range(1, len(s) + 1))
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v -k series`
Expected: FAIL — `has no attribute 'series'`.

- [ ] **Step 3: Implémenter `series()`**

```python
# ajouter à poc/famat/famat_recipes.py

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
```

- [ ] **Step 4: Vérifier que tout passe**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v`
Expected: PASS (tous). Si `test_series_detects_segment_break_on_jump` échoue sur le nombre de segments, ajuster `jump_k` du test (pas de l'API) — le saut synthétique de 0.059 contre une médiane de 0.001 donne un ratio 59, largement > 5.

- [ ] **Step 5: Commit**

```bash
git add poc/famat
git commit -m "feat(famat-poc): série par pièce + segmentation par ruptures (re-réglages)"
```

---

### Task 3: `drift()` — régression par segment, limites ±3σ, extrapolation

**Files:**
- Modify: `poc/famat/famat_recipes.py`
- Test: `poc/famat/tests/test_recipes.py`

**Interfaces:**
- Consumes: `series()` (Task 2), `load_rows`/`load_csv` (Task 1).
- Produces: `drift(con, cle: str, chapter: int, k_sigma: float = 3.0, jump_k: float = 5.0) -> dict` :
  ```python
  {
    "cle": str, "chapter": int, "n_parts": int,
    "mean": float, "sigma": float, "lcl": float, "ucl": float,
    "series": [...],                     # sortie de series()
    "segments": [{"segment_id": int, "n": int,
                  "slope_per_part": float, "intercept": float}],
    "current": {"segment_id": int, "slope_per_part": float,
                "last_value": float, "last_part_index": int,
                "parts_to_limit": float | None,   # None si pente ~0 ou s'éloigne
                "beyond_limits": bool}
  }
  ```

- [ ] **Step 1: Tests qui échouent**

```python
# ajouter à poc/famat/tests/test_recipes.py

def test_drift_slope_per_segment_and_extrapolation():
    # segment 1 : pente 0.001/pièce (20 pièces), segment 2 : pente 0.002/pièce (20 pièces)
    rows = [_mk(i + 1, f"P{i:02d}", 0.001 * i, i) for i in range(20)]
    rows += [_mk(i + 21, f"Q{i:02d}", -0.100 + 0.002 * i, i + 20) for i in range(20)]
    d = fr.drift(fr.load_rows(rows), "CORRECTION_X", 10)
    assert d["n_parts"] == 40
    assert len(d["segments"]) == 2
    assert d["segments"][0]["slope_per_part"] == pytest.approx(0.001, rel=1e-6)
    assert d["segments"][1]["slope_per_part"] == pytest.approx(0.002, rel=1e-6)
    cur = d["current"]
    assert cur["segment_id"] == 1
    assert cur["slope_per_part"] == pytest.approx(0.002, rel=1e-6)
    # pente positive, dernière valeur sous l'UCL -> extrapolation finie et positive
    assert cur["parts_to_limit"] is not None and cur["parts_to_limit"] > 0


def test_drift_no_extrapolation_when_flat():
    rows = [_mk(i + 1, f"P{i:02d}", 0.005, i) for i in range(15)]  # série plate
    d = fr.drift(fr.load_rows(rows), "CORRECTION_X", 10)
    assert d["current"]["parts_to_limit"] is None


@real_data
def test_drift_real_correction_x_chapter10():
    d = fr.drift(fr.load_csv(CSV), "CORRECTION_X", 10)
    assert d["n_parts"] >= 50
    assert d["sigma"] > 0
    assert d["lcl"] < d["mean"] < d["ucl"]
    for seg in d["segments"]:
        assert seg["n"] >= 1
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v -k drift`
Expected: FAIL — `has no attribute 'drift'`.

- [ ] **Step 3: Implémenter `drift()`**

```python
# ajouter à poc/famat/famat_recipes.py

def drift(con, cle: str, chapter: int, k_sigma: float = 3.0, jump_k: float = 5.0) -> dict:
    """Analyse de dérive : régression par segment + limites ±k_sigma + extrapolation."""
    s = series(con, cle, chapter, jump_k)
    if not s:
        return {"cle": cle, "chapter": chapter, "n_parts": 0, "series": [],
                "segments": [], "current": None, "mean": None, "sigma": None,
                "lcl": None, "ucl": None}

    seg_con = load_rows([])  # connexion de travail vide
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
```

- [ ] **Step 4: Vérifier que tout passe**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v`
Expected: PASS. Note : dans le test synthétique 1, la pente du segment courant (0.002) avec sigma calculé sur toute la série peut donner `parts_to_limit` très grand — le test ne vérifie que la finitude et le signe.

- [ ] **Step 5: Commit**

```bash
git add poc/famat
git commit -m "feat(famat-poc): drift() — régression par segment, limites ±3σ, extrapolation"
```

---

### Task 4: `backtest()` — « on aurait alerté N pièces avant »

**Files:**
- Modify: `poc/famat/famat_recipes.py`
- Test: `poc/famat/tests/test_recipes.py`

**Interfaces:**
- Consumes: `series()` (Task 2).
- Produces: `backtest(con, cle: str, chapter: int, k_sigma: float = 3.0, horizon: int = 5, min_train: int = 10, window: int = 10, jump_k: float = 5.0) -> dict` :
  ```python
  {
    "cle": str, "chapter": int, "n_parts": int,
    "alerts": [{"part_index": int, "predicted_crossing_at": float}],
    "crossings": [int],          # part_index des franchissements réels
    "true_alerts": int,          # alertes suivies d'un franchissement réel <= horizon
    "false_alerts": int,
    "lead_times": [int],         # anticipation en nombre de pièces, par franchissement anticipé
    "missed_crossings": int
  }
  ```
  Rejeu chronologique : à chaque pièce `i >= min_train`, limites ±kσ calculées sur `[1..i]` **uniquement**, régression sur les **`window` derniers points du segment courant** dans `[1..i]` (le taux de dérive *actuel* — une régression sur tout le segment diluerait la dérive récente dans l'historique stable) ; alerte si la valeur extrapolée à `i + horizon` franchit une limite alors que la valeur courante est encore dedans.

- [ ] **Step 1: Tests qui échouent**

```python
# ajouter à poc/famat/tests/test_recipes.py

def test_backtest_detects_upcoming_crossing():
    # 30 pièces stables autour de 0 puis dérive douce +0.0005/pièce : le
    # franchissement de l'UCL doit être anticipé AVANT d'arriver (dérive
    # trop brutale = premier point déjà hors limites = inanticipable)
    rows = [_mk(i + 1, f"P{i:02d}", 0.001 * ((i % 3) - 1), i) for i in range(30)]
    rows += [_mk(i + 31, f"Q{i:02d}", 0.0005 * (i + 1), i + 30) for i in range(15)]
    b = fr.backtest(fr.load_rows(rows), "CORRECTION_X", 10, jump_k=50.0)  # jump_k haut : pas de coupure de segment
    assert b["n_parts"] == 45
    assert len(b["crossings"]) >= 1
    assert b["true_alerts"] >= 1
    assert all(lt >= 1 for lt in b["lead_times"])


def test_backtest_quiet_series_no_alert():
    rows = [_mk(i + 1, f"P{i:02d}", 0.001 * ((i % 3) - 1), i) for i in range(40)]
    b = fr.backtest(fr.load_rows(rows), "CORRECTION_X", 10)
    assert b["true_alerts"] == 0 and b["false_alerts"] == 0


@real_data
def test_backtest_real_runs():
    b = fr.backtest(fr.load_csv(CSV), "CORRECTION_X", 10)
    assert b["n_parts"] >= 50
    assert b["false_alerts"] >= 0  # structure saine, pas d'exception
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v -k backtest`
Expected: FAIL — `has no attribute 'backtest'`.

- [ ] **Step 3: Implémenter `backtest()`**

```python
# ajouter à poc/famat/famat_recipes.py
import statistics


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
```

- [ ] **Step 4: Vérifier que tout passe**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v`
Expected: PASS. Si `test_backtest_detects_upcoming_crossing` échoue : imprimer `b` et vérifier la mécanique — la dérive doit être assez douce pour que des points restent DANS les limites pendant qu'elle s'installe (sinon « déjà dehors », inanticipable) et assez franche pour finir par franchir l'UCL finale. Ajuster la pente synthétique dans [0.0003, 0.001] ou allonger la queue de dérive (15 → 25 points) — jamais assouplir l'assertion `true_alerts >= 1`.

- [ ] **Step 5: Commit**

```bash
git add poc/famat
git commit -m "feat(famat-poc): backtest() — alertes anticipées, vrais/faux positifs, lead time"
```

---

### Task 5: `temperature_restarts()` — corrélation température ↔ redémarrages

**Files:**
- Modify: `poc/famat/famat_recipes.py`
- Test: `poc/famat/tests/test_recipes.py`

**Interfaces:**
- Consumes: `load_csv`/`load_rows` (Task 1).
- Produces: `temperature_restarts(con) -> dict` :
  ```python
  {
    "n_restarts": int,                  # total des retours en arrière de chapitre
    "n_serials_with_restart": int,
    "mean_temp_at_restart": float | None,   # TEMP_PIECE la plus récente avant chaque redémarrage
    "mean_temp_overall": float | None,
    "corr_restarts_temp": float | None,     # Pearson par pièce : nb redémarrages vs temp moyenne
    "per_serial": [{"serial": str, "restarts": int, "mean_temp": float | None}]
  }
  ```

- [ ] **Step 1: Tests qui échouent**

```python
# ajouter à poc/famat/tests/test_recipes.py

def test_temperature_restarts_synthetic():
    rows = [
        # S1 : chapitres 1,2,3 puis retour à 1 (1 redémarrage), temp élevée avant
        (1, "S1", 1, "COTR_X", 0.0, "2026-01-01 10:00:00"),
        (2, "S1", 2, "COTR_X", 0.0, "2026-01-01 10:01:00"),
        (3, "S1", None, "TEMP_PIECE", 24.0, "2026-01-01 10:02:00"),
        (4, "S1", 3, "COTR_X", 0.0, "2026-01-01 10:03:00"),
        (5, "S1", 1, "COTR_X", 0.0, "2026-01-01 10:04:00"),  # <- redémarrage
        # S2 : progression monotone, temp basse, aucun redémarrage
        (6, "S2", 1, "COTR_X", 0.0, "2026-01-01 11:00:00"),
        (7, "S2", None, "TEMP_PIECE", 17.0, "2026-01-01 11:01:00"),
        (8, "S2", 2, "COTR_X", 0.0, "2026-01-01 11:02:00"),
    ]
    t = fr.temperature_restarts(fr.load_rows(rows))
    assert t["n_restarts"] == 1
    assert t["n_serials_with_restart"] == 1
    assert t["mean_temp_at_restart"] == pytest.approx(24.0)


@real_data
def test_temperature_restarts_real():
    t = fr.temperature_restarts(fr.load_csv(CSV))
    assert t["n_serials_with_restart"] == 94          # invariant profilé : 94/94
    assert 400 <= t["n_restarts"] <= 600              # ~5.4 × 94
    assert 14 <= t["mean_temp_overall"] <= 26
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v -k temperature`
Expected: FAIL — `has no attribute 'temperature_restarts'`.

- [ ] **Step 3: Implémenter**

```python
# ajouter à poc/famat/famat_recipes.py

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
```

- [ ] **Step 4: Vérifier que tout passe**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v`
Expected: PASS. Si l'invariant `400 <= n_restarts <= 600` échoue, imprimer la valeur réelle, la vérifier contre `mean 5.4 × 94 ≈ 508`, ajuster la fourchette du test si la définition diffère (transitions vs événements) — documenter le choix en commentaire.

- [ ] **Step 5: Commit**

```bash
git add poc/famat
git commit -m "feat(famat-poc): temperature_restarts() — corrélation température/redémarrages"
```

---

### Task 6: `spc_chart()` — carte de contrôle matplotlib (SVG + PNG)

**Files:**
- Modify: `poc/famat/famat_recipes.py`
- Test: `poc/famat/tests/test_recipes.py`

**Interfaces:**
- Consumes: sortie de `drift()` (Task 3).
- Produces: `spc_chart(drift_result: dict, out_dir: str = "artifacts", fmt: str = "svg") -> str` — trace la carte de contrôle (points par pièce, ligne centrale, bandes ±kσ, points hors limites en rouge, droites de régression par segment annotées de la pente en µm/pièce, traits verticaux aux débuts de segment), sauve `spc_<cle>_ch<chapter>.<fmt>` dans `out_dir`, retourne le chemin. `out_dir="artifacts"` = convention de collecte du sandbox CodeExec.

- [ ] **Step 1: Test qui échoue**

```python
# ajouter à poc/famat/tests/test_recipes.py
import tempfile


def test_spc_chart_writes_svg_and_png():
    rows = [_mk(i + 1, f"P{i:02d}", 0.001 * i, i) for i in range(25)]
    d = fr.drift(fr.load_rows(rows), "CORRECTION_X", 10)
    with tempfile.TemporaryDirectory() as td:
        svg = fr.spc_chart(d, out_dir=td, fmt="svg")
        png = fr.spc_chart(d, out_dir=td, fmt="png")
        assert svg.endswith("spc_CORRECTION_X_ch10.svg") and os.path.getsize(svg) > 1000
        assert png.endswith("spc_CORRECTION_X_ch10.png") and os.path.getsize(png) > 1000
        with open(svg, encoding="utf-8", errors="ignore") as f:
            assert "<svg" in f.read(500)
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v -k spc_chart`
Expected: FAIL — `has no attribute 'spc_chart'`.

- [ ] **Step 3: Implémenter**

```python
# ajouter à poc/famat/famat_recipes.py
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


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
```

- [ ] **Step 4: Vérifier que tout passe + inspection visuelle sur données réelles**

Run: `uv run --with duckdb python -m pytest poc/famat/tests/test_recipes.py -v`
Expected: PASS.

Puis générer une carte réelle et l'ouvrir :
```bash
uv run --with duckdb python -c "
import sys; sys.path.insert(0, 'poc/famat')
import famat_recipes as fr
d = fr.drift(fr.load_csv('poc/famat/data/Payload-20260526.csv'), 'CORRECTION_X', 10)
print(fr.spc_chart(d, out_dir='poc/famat/data', fmt='png'))"
open poc/famat/data/spc_CORRECTION_X_ch10.png
```
Vérifier : points lisibles, bandes visibles, pentes annotées, pas de chevauchement de texte. Ajuster figsize/fontsize si nécessaire.

- [ ] **Step 5: Commit**

```bash
git add poc/famat
git commit -m "feat(famat-poc): spc_chart() — carte de contrôle SPC SVG/PNG"
```

---

### Task 7: Image sandbox custom (duckdb + pymysql + recettes cuites)

**Files:**
- Modify: `agent/sandbox/sandbox_base_image/python/requirements.txt`
- Modify: `agent/sandbox/sandbox_base_image/python/Dockerfile`
- Create: `agent/sandbox/.env` (depuis `.env.example`, non commité si gitignoré — vérifier)

**Interfaces:**
- Consumes: `poc/famat/famat_recipes.py` (Tasks 1-6, module autonome).
- Produces: image Docker `sandbox-base-python:latest` avec `import famat_recipes` fonctionnel.

- [ ] **Step 1: Ajouter les dépendances**

`agent/sandbox/sandbox_base_image/python/requirements.txt` devient :
```
numpy
pandas
matplotlib
requests
duckdb
pymysql
```

- [ ] **Step 2: Cuire les recettes dans l'image**

Dans `agent/sandbox/sandbox_base_image/python/Dockerfile`, après la ligne `COPY requirements.txt .` ajouter :
```dockerfile
# CUSTOM B2B SaaS — recettes FAMAT cuites dans l'image sandbox (POC dérive process)
COPY famat_recipes.py /usr/local/lib/python3.11/site-packages/famat_recipes.py
```
Et copier le module à côté du Dockerfile (le contexte de build est `./sandbox_base_image/python`) :
```bash
cp poc/famat/famat_recipes.py agent/sandbox/sandbox_base_image/python/famat_recipes.py
```
Note : cette copie est un artefact de build régénérable — ajouter en tête du fichier copié un commentaire `# GÉNÉRÉ depuis poc/famat/famat_recipes.py — ne pas éditer ici`. Le `cp` doit être refait à chaque modification des recettes (documenté dans `poc/famat/README.md`).

- [ ] **Step 3: Créer le .env sandbox avec des limites adaptées**

```bash
cd agent/sandbox && cp .env.example .env
```
Puis dans `agent/sandbox/.env`, modifier :
```
SANDBOX_MAX_MEMORY=1g
SANDBOX_TIMEOUT=60s
SANDBOX_EXECUTOR_MANAGER_POOL_SIZE=3
```
(256m/10s par défaut : trop juste pour duckdb + matplotlib sur 90k lignes.)

- [ ] **Step 4: Builder les images de base (NEED_MIRROR=0 — on n'est pas en Chine)**

```bash
cd agent/sandbox
docker build --build-arg NEED_MIRROR=0 -t sandbox-base-python:latest ./sandbox_base_image/python
docker build -t sandbox-base-nodejs:latest ./sandbox_base_image/nodejs
```

- [ ] **Step 5: Vérifier les imports dans l'image**

Run:
```bash
docker run --rm sandbox-base-python:latest python -c \
  "import duckdb, pymysql, matplotlib, famat_recipes; print('OK', duckdb.__version__)"
```
Expected: `OK <version>`.

- [ ] **Step 6: Commit**

```bash
git status --short agent/sandbox   # vérifier que .env n'est PAS suivi ; l'ajouter au .gitignore sinon
git add agent/sandbox/sandbox_base_image poc/famat/README.md
git commit -m "feat(famat-poc): image sandbox custom — duckdb, pymysql, recettes FAMAT cuites"
```

---

### Task 8: Executor-manager en marche + smoke test HTTP

**Files:**
- Aucun fichier de code — validation d'infra (commandes uniquement).

**Interfaces:**
- Consumes: images de la Task 7.
- Produces: endpoint `http://localhost:9385` opérationnel, utilisé par la Task 9.

- [ ] **Step 1: Builder et démarrer l'executor-manager**

```bash
cd agent/sandbox
docker compose up -d --build
docker compose ps   # attendre healthy
```

- [ ] **Step 2: Healthcheck**

Run: `curl -s http://localhost:9385/healthz`
Expected: réponse 200 (corps type `{"status":"healthy"}` — accepter toute 2xx).

- [ ] **Step 3: Smoke run — recettes importables dans le conteneur d'exécution**

```bash
CODE=$(python3 - <<'PY'
import base64, json
code = """
import famat_recipes, duckdb, json
def main():
    return json.dumps({"duckdb": duckdb.__version__, "recipes": hasattr(famat_recipes, "drift")})
"""
print(json.dumps({"code_b64": base64.b64encode(code.encode()).decode(), "language": "python"}))
PY
)
curl -s -X POST http://localhost:9385/run -H 'Content-Type: application/json' -d "$CODE"
```
Expected: stdout contenant `"recipes": true`. Si le format de requête diffère (champ `code` vs `code_b64`, champ `arguments`), lire `agent/sandbox/executor_manager/` pour le schéma exact et ajuster — noter le format dans `poc/famat/README.md`.

- [ ] **Step 4: Vérifier l'accès réseau sortant du conteneur d'exécution**

Même mécanique avec le code :
```python
import socket, json
def main():
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=3).close()
        return json.dumps({"network": True})
    except OSError as e:
        return json.dumps({"network": False, "error": str(e)})
```
Expected: `"network": true`. **Si false** : les recettes ne pourront pas atteindre la base Cyllene depuis le sandbox — bloquant pour la Task 10, à résoudre avant de continuer (options : réseau du compose sandbox, politique seccomp, DNS).

- [ ] **Step 5: Documenter dans le README et committer**

Ajouter à `poc/famat/README.md` la section « Sandbox » : commandes de build/up, format de `/run` constaté, résultat du test réseau.
```bash
git add poc/famat/README.md
git commit -m "docs(famat-poc): sandbox executor-manager — démarrage et smoke tests"
```

---

### Task 9: Provider self_managed dans RAGFlow + smoke bout-en-bout

**Files:**
- Aucun fichier de code — configuration via l'admin UI + validation.

**Interfaces:**
- Consumes: endpoint Task 8 ; stack dev RAGFlow.
- Produces: `code_exec` fonctionnel depuis un canvas RAGFlow, artefacts collectés.

- [ ] **Step 1: Démarrer le stack dev**

```bash
bash scripts/dev_up.sh
```
(Utiliser les scripts custom du repo — pas de démarrage service par service.)

- [ ] **Step 2: Configurer le provider dans l'admin**

Dans le navigateur : `http://localhost:<port web>/admin` → page **Sandbox settings** (routes `/api/v1/admin/sandbox/*`). Choisir provider **self_managed**, endpoint `http://localhost:9385` (le serveur API tourne sur le host en dev — si la connexion échoue, essayer `http://host.docker.internal:9385` puis lire la config réseau du compose). Cliquer **Test connection** → succès attendu.

- [ ] **Step 3: Smoke canvas minimal**

Dans l'UI agent : créer un agent vide « SMOKE sandbox », composant Agent avec le seul tool `code_exec`, prompt système : `Exécute exactement le code Python fourni par l'utilisateur via le tool code_exec, sans le modifier.` En chat, envoyer :

```
import famat_recipes, json
def main():
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT 1 AS a")
    return json.dumps({"ok": True})
```

Expected: réponse contenant `"ok": true`.

- [ ] **Step 4: Smoke artefact graphique (SVG puis PNG)**

Même canvas, envoyer un code qui génère une carte synthétique :
```
import famat_recipes as fr, json
def main():
    rows = [(i+1, f"P{i:02d}", 10, "CORRECTION_X", 0.001*i, f"2026-01-01 10:{i:02d}:00") for i in range(25)]
    d = fr.drift(fr.load_rows(rows), "CORRECTION_X", 10)
    svg = fr.spc_chart(d, out_dir="artifacts", fmt="svg")
    png = fr.spc_chart(d, out_dir="artifacts", fmt="png")
    return json.dumps({"svg": svg, "png": png})
```
Expected: le chat affiche la ou les images en pièces jointes. **Noter si le SVG s'affiche** (décision spec : SVG si supporté, sinon PNG — figer le `fmt` par défaut des recettes en conséquence et mettre à jour `poc/famat/famat_recipes.py` + recopier dans l'image si changement).

- [ ] **Step 5: Documenter + commit**

Ajouter au README : provider configuré, endpoint retenu, verdict SVG/PNG.
```bash
git add poc/famat/README.md
git commit -m "docs(famat-poc): provider self_managed opérationnel, verdict artefacts SVG/PNG"
```

---

### Task 10: Source de données par URL (fichier servi par MinIO) — base Cyllene différée

> **Révision 2026-08-11 (décision utilisateur)** : la base Cyllene n'est pas accessible depuis ce poste (ouvertures de flux nécessaires en prod). Le POC fonctionne sur fichier : le CSV est déposé dans le MinIO du stack dev (la donnée reste locale) et les recettes le chargent par HTTP depuis le sandbox via `load_url()`. `load_db()` (chemin prod, pymysql) reste dans le plan d'origine ci-dessous À TITRE DOCUMENTAIRE et n'est PAS implémenté tant que les flux ne sont pas ouverts — le code de cette task est `load_url()` uniquement.

**Files:**
- Modify: `poc/famat/famat_recipes.py` (ajout `load_url`)
- Test: `poc/famat/tests/test_recipes.py`
- Modify: `agent/sandbox/sandbox_base_image/python/famat_recipes.py` (recopie) + rebuild image

**Interfaces (révisées):**
- Produces: `load_url(url: str, timeout: int = 60) -> duckdb.DuckDBPyConnection` — télécharge le CSV webhook (via `requests`, déjà dans l'image sandbox et non banni par l'AST security) vers un fichier temporaire puis délègue à `load_csv()` ; même table `events`.
- Produces: le CSV uploadé dans le MinIO du stack dev (bucket `famat-poc`), URL stable accessible DEPUIS le conteneur sandbox (à déterminer empiriquement : `host.containers.internal`, IP de gateway, ou IP LAN du host — tester via POST /run).

**Steps (révisés):**

- [ ] **Step 1: Test qui échoue**

```python
# ajouter à poc/famat/tests/test_recipes.py

@real_data
def test_load_url_matches_load_csv(tmp_path, monkeypatch):
    # sert le CSV local par HTTP éphémère et vérifie l'équivalence avec load_csv
    import threading, functools, http.server
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=os.path.dirname(CSV))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/{os.path.basename(CSV)}"
        con = fr.load_url(url)
        assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 90375
        assert con.execute("SELECT count(DISTINCT serial) FROM events").fetchone()[0] == 94
    finally:
        srv.shutdown()
```

- [ ] **Step 2: Vérifier l'échec** — `uv run --with duckdb --with requests python -m pytest poc/famat/tests/test_recipes.py -v -k load_url` → FAIL `has no attribute 'load_url'`.

- [ ] **Step 3: Implémenter `load_url()`**

```python
# ajouter à poc/famat/famat_recipes.py

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
```

- [ ] **Step 4: Tests verts + recopie image + rebuild** — suite complète verte, puis `cp poc/famat/famat_recipes.py agent/sandbox/sandbox_base_image/python/famat_recipes.py` (ré-ajouter le header GÉNÉRÉ), rebuild `sandbox-base-python:latest` (`--load` si buildx), `docker compose up -d --force-recreate` dans agent/sandbox.

- [ ] **Step 5: Uploader le CSV dans MinIO + URL joignable du sandbox** — utiliser le MinIO du stack dev (credentials dans docker/.env) : créer le bucket `famat-poc`, uploader `Payload-20260526.csv`, rendre l'objet téléchargeable (politique anonyme sur le bucket OU URL présignée longue durée). Depuis le sandbox (POST /run), tester `famat_recipes.load_url("<URL candidate>")` avec les candidates dans l'ordre : `http://host.containers.internal:<port>`, `http://host.docker.internal:<port>`, IP de gateway du réseau du conteneur. Retenir la première qui marche → c'est la `DATA_URL` du POC. Attendu : `rows == 90375` retourné par le sandbox.

- [ ] **Step 6: Documenter + commit** — README section « Source de données » : URL retenue, procédure d'upload MinIO, bascule future vers `load_db()` (base Cyllene, une ligne dans le prompt). Commit sans binaire ni secret (les credentials MinIO du stack dev sont déjà dans docker/.env non commité — référencer, ne pas copier).

---

#### (Archive — chemin prod base Cyllene, NON implémenté dans ce POC)
### ~~Task 10 (original): Chargement depuis la base Cyllene~~

**Files:**
- Modify: `poc/famat/famat_recipes.py`
- Test: `poc/famat/tests/test_recipes.py`
- Modify: `agent/sandbox/sandbox_base_image/python/famat_recipes.py` (recopie) + rebuild image

**Interfaces:**
- Consumes: infos de connexion réelles (moteur, host, port, base, table, credentials lecture seule — fournies par l'utilisateur au moment de l'exécution de cette task).
- Produces: `load_db(host: str, port: int, user: str, password: str, database: str, table: str = "WebhookMesure") -> duckdb.DuckDBPyConnection` — même table `events` que `load_csv`, via pymysql. **Si le moteur réel n'est pas MySQL/MariaDB** : adapter le driver (documenter), même signature.

- [ ] **Step 1: Découvrir le moteur et le schéma réels**

Demander/récupérer : type de moteur, host:port, nom de base, nom exact de la table et des 3 colonnes, credentials lecture seule. Vérifier la connectivité depuis le host :
```bash
uv run python -c "
import pymysql
con = pymysql.connect(host='<HOST>', port=<PORT>, user='<USER>', password='<PWD>', database='<DB>')
cur = con.cursor(); cur.execute('SELECT count(*) FROM <TABLE>'); print(cur.fetchone())"
```
Expected: un compte ≥ 90375 (la base vit, le webhook continue d'alimenter).

- [ ] **Step 2: Test qui échoue (skippé sans variables d'env)**

```python
# ajouter à poc/famat/tests/test_recipes.py

DB = {k: os.environ.get(f"FAMAT_DB_{k}") for k in ("HOST", "PORT", "USER", "PASSWORD", "NAME", "TABLE")}
has_db = all(DB[k] for k in ("HOST", "PORT", "USER", "PASSWORD", "NAME"))
real_db = pytest.mark.skipif(not has_db, reason="variables FAMAT_DB_* absentes")


@real_db
def test_load_db_matches_csv_profile():
    con = fr.load_db(DB["HOST"], int(DB["PORT"]), DB["USER"], DB["PASSWORD"],
                     DB["NAME"], DB["TABLE"] or "WebhookMesure")
    n = con.execute("SELECT count(*) FROM events").fetchone()[0]
    assert n >= 90375
    n_serials = con.execute("SELECT count(DISTINCT serial) FROM events").fetchone()[0]
    assert n_serials >= 94
```

- [ ] **Step 3: Vérifier l'échec**

Run: `FAMAT_DB_HOST=... FAMAT_DB_PORT=... FAMAT_DB_USER=... FAMAT_DB_PASSWORD=... FAMAT_DB_NAME=... uv run --with duckdb --with pymysql python -m pytest poc/famat/tests/test_recipes.py -v -k load_db`
Expected: FAIL — `has no attribute 'load_db'`.

- [ ] **Step 4: Implémenter `load_db()`**

```python
# ajouter à poc/famat/famat_recipes.py

def load_db(host: str, port: int, user: str, password: str, database: str,
            table: str = "WebhookMesure") -> duckdb.DuckDBPyConnection:
    """Base SQL Cyllene (webhook) -> table `events` (même schéma que load_csv)."""
    import pymysql

    src = pymysql.connect(host=host, port=port, user=user, password=password,
                          database=database, charset="utf8mb4")
    try:
        with src.cursor() as cur:
            cur.execute(
                f"SELECT WebhookMesure_Id, WebhookMesure_Contenu, WebhookMesure_DateCreation FROM {table}"
            )
            raw = cur.fetchall()
    finally:
        src.close()

    con = duckdb.connect()
    con.execute("CREATE TABLE raw (seq BIGINT, contenu VARCHAR, ts TIMESTAMP)")
    con.executemany("INSERT INTO raw VALUES (?, ?, ?)", list(raw))
    con.execute(
        """
        CREATE TABLE events AS
        SELECT seq,
               json_extract_string(contenu, '$.Serial') AS serial,
               TRY_CAST(json_extract_string(contenu, '$.Chapter') AS INTEGER) AS chapter,
               json_extract_string(contenu, '$.cle') AS cle,
               TRY_CAST(json_extract_string(contenu, '$.value') AS DOUBLE) AS value_num,
               ts
        FROM raw
        """
    )
    return con
```
Si le moteur réel est MSSQL/Postgres : remplacer le bloc pymysql par le driver adapté (pymssql/psycopg2), garder la signature et le reste identique, mettre à jour requirements de l'image, documenter dans le README.

- [ ] **Step 5: Vérifier que le test passe + recopier dans l'image + rebuild**

Run: la commande du Step 3.
Expected: PASS.
```bash
cp poc/famat/famat_recipes.py agent/sandbox/sandbox_base_image/python/famat_recipes.py
cd agent/sandbox && docker build --build-arg NEED_MIRROR=0 -t sandbox-base-python:latest ./sandbox_base_image/python && docker compose up -d --force-recreate
```
Puis vérifier depuis le sandbox (smoke canvas de la Task 9) :
```
import famat_recipes as fr, json
def main():
    con = fr.load_db("<HOST>", <PORT>, "<USER>", "<PWD>", "<DB>")
    return json.dumps({"rows": con.execute("SELECT count(*) FROM events").fetchone()[0]})
```
Expected: `rows >= 90375`. Si échec réseau : revenir au diagnostic Task 8 Step 4.

- [ ] **Step 6: Commit**

```bash
git add poc/famat agent/sandbox/sandbox_base_image
git commit -m "feat(famat-poc): load_db() — chargement direct depuis la base Cyllene"
```

---

### Task 11: Canvas agent FAMAT (DSL + prompt système)

**Files:**
- Create: `poc/famat/famat_agent_canvas.json` (export DSL — versionné)
- Create: `poc/famat/system_prompt.md` (source du prompt — versionné)

**Interfaces:**
- Consumes: recettes cuites dans l'image (Tasks 7/10), provider configuré (Task 9), connexion Cyllene (Task 10).
- Produces: agent importable dans n'importe quel tenant (fichier DSL), répondant aux 3 analyses canoniques.

- [ ] **Step 1: Écrire le prompt système**

Créer `poc/famat/system_prompt.md` avec ce contenu (remplacer les `<...>` par les vraies valeurs de connexion au moment de l'import — PAS dans le fichier versionné) :

```markdown
Tu es l'assistant qualité process de FAMAT (usinage aéronautique). Tu analyses le flux
de mesures machine (palpage, corrections outil, températures) pour détecter les dérives
process et expliquer les non-conformités. Tu réponds TOUJOURS en français, en langage
qualiticien (SPC, cartes de contrôle, limites ±3σ), précis et chiffré.

## Données
Base SQL alimentée par webhook, table `<TABLE>` : une ligne par événement,
JSON {"Serial": pièce, "Chapter": étape d'usinage 0-57, "cle": nom de mesure, "value": valeur}.
Clés importantes : CORRECTION_X/Z (correction outil par chapitre — signal de dérive),
COTR_X/Z (cotes mesurées), JAUGE_X/Z (jauges outil), TEMP_PIECE/TEMP_ETALON (températures),
RAYON_OUT. ~94 pièces, ~90 000 événements, chapitres 0→57.
Un "redémarrage" = le chapitre redescend dans la séquence d'une pièce (perte de temps,
corrélée à la température atelier).

## Recettes canoniques — utilise le tool code_exec avec EXACTEMENT ces codes
Le module `famat_recipes` est préinstallé dans le sandbox. Connexion :
CFG = dict(host="<HOST>", port=<PORT>, user="<USER>", password="<PWD>", database="<DB>")

### Recette 1 — Dérive d'une clé à un chapitre (défaut : CORRECTION_X, chapitre 10)
import famat_recipes as fr, json
def main():
    con = fr.load_db(**CFG)
    d = fr.drift(con, "<CLE>", <CHAPITRE>)
    fr.spc_chart(d, out_dir="artifacts")
    d.pop("series")  # ne pas renvoyer les points bruts
    return json.dumps(d, default=str)

### Recette 2 — Backtest des alertes (la preuve d'anticipation)
import famat_recipes as fr, json
def main():
    con = fr.load_db(**CFG)
    b = fr.backtest(con, "<CLE>", <CHAPITRE>)
    b["alerts"] = b["alerts"][:20]
    return json.dumps(b, default=str)

### Recette 3 — Température ↔ redémarrages
import famat_recipes as fr, json
def main():
    con = fr.load_db(**CFG)
    t = fr.temperature_restarts(con)
    t["per_serial"] = sorted(t["per_serial"], key=lambda x: -x["restarts"])[:15]
    return json.dumps(t, default=str)

## Règles
- Pour les 3 analyses ci-dessus : recopie la recette TELLE QUELLE, en remplaçant
  uniquement <CLE> et <CHAPITRE> selon la demande. N'invente JAMAIS une autre méthode.
- Pour toute autre question sur les données : utilise le tool execute_sql avec des
  requêtes AGRÉGÉES (count, avg, min/max, group by) — jamais de SELECT * massif.
- Interprète toujours les résultats : pente en µm/pièce, pièces restantes avant limite,
  vrais/faux positifs du backtest, écart de température aux redémarrages.
- Si une carte est générée, mentionne-la dans ta réponse.
- Si la demande est ambiguë (clé ou chapitre manquant), propose CORRECTION_X chapitre 10
  et dis pourquoi (signal de dérive le plus dense : 99 % de valeurs non nulles).
```

- [ ] **Step 2: Construire le canvas dans l'UI**

Dans l'UI agent RAGFlow : créer un agent « FAMAT — Dérive process » (partir de zéro ou du template *Text2SQL data expert* pour la structure Agent+tools) :
- Composant **Agent** : modèle = LLM du workspace avec tool-calling ; prompt système = contenu de `system_prompt.md` avec les `<...>` remplacés ; température basse (0.1-0.2).
- Tool **code_exec** : langage Python, timeout 60 s.
- Tool **execute_sql** : db_type selon le moteur réel, host/port/database/credentials Cyllene, `max_records` = 2000 (exploration agrégée seulement).
- Composant **Begin** puis Agent puis **Message** selon le pattern du template.

- [ ] **Step 3: Tester les 3 recettes en chat**

Poser dans l'ordre, vérifier chaque réponse :
1. « Analyse la dérive de CORRECTION_X au chapitre 10 » → carte SPC jointe + pente(s) en µm/pièce + pièces restantes avant limite.
2. « Fais le backtest des alertes sur cette cote » → vrais/faux positifs + anticipation moyenne en pièces.
3. « Montre la corrélation entre température et redémarrages » → nb de redémarrages, températures comparées, corrélation.
Expected: les 3 réponses citent des chiffres cohérents avec les tests offline (mêmes ordres de grandeur), le code exécuté est celui des recettes (vérifier dans les logs/trace du canvas).

- [ ] **Step 4: Exporter le DSL et le versionner (sans secrets)**

Exporter le canvas depuis l'UI → `poc/famat/famat_agent_canvas.json`. **Remplacer les credentials par des placeholders `<HOST>/<USER>/<PWD>`** dans le fichier versionné (le prompt système ET la config ExeSQL). Vérifier :
```bash
grep -iE "password|<PWD>" poc/famat/famat_agent_canvas.json   # ne doit montrer que des placeholders
```

- [ ] **Step 5: Commit**

```bash
git add poc/famat/famat_agent_canvas.json poc/famat/system_prompt.md
git commit -m "feat(famat-poc): canvas agent FAMAT — prompt recettes + DSL versionné (sans secrets)"
```

---

### Task 12: Fiabilisation + répétition générale

**Files:**
- Create: `poc/famat/DEMO.md` (déroulé de démo + résultats attendus)

**Interfaces:**
- Consumes: agent complet (Task 11).
- Produces: démo rejouable, checklist validée, chiffres de référence notés.

- [ ] **Step 1: Écrire le déroulé de démo**

Créer `poc/famat/DEMO.md` :
```markdown
# Démo FAMAT — déroulé

## Setup (avant la réunion)
- [ ] `cd agent/sandbox && docker compose up -d` puis healthz OK
- [ ] Stack RAGFlow up, provider sandbox testé (admin → Test connection)
- [ ] Connectivité base Cyllene vérifiée (recette 1 à blanc)
- [ ] Chat vierge ouvert sur l'agent « FAMAT — Dérive process »

## Déroulé (questions dans l'ordre)
1. « Analyse la dérive de CORRECTION_X au chapitre 10 »       → attendu : <à remplir Task 12>
2. « Fais le backtest des alertes sur cette cote »            → attendu : <à remplir Task 12>
3. « Montre la corrélation entre température et redémarrages »→ attendu : <à remplir Task 12>
4. Question libre du client (exploration execute_sql)         → montrer la trace d'exécution
5. Teaser phase 2 : KB gammes/procédures croisée avec la dérive

## Chiffres de référence (validés offline)
<à remplir : sortie des tests réels Tasks 3-5>
```

- [ ] **Step 2: Rejouer 3 fois le déroulé complet**

Trois passes complètes en chat, chronométrées. Expected : chaque analyse < 60 s, réponses stables entre les passes (mêmes chiffres — le code est déterministe, seule la rédaction LLM varie), aucune déviation de recette.

- [ ] **Step 3: Corriger les déviations constatées**

Pour chaque déviation (le LLM réécrit la recette, oublie la carte, répond en anglais…) : durcir le prompt système (`poc/famat/system_prompt.md`), re-tester. Si le LLM réécrit systématiquement les recettes malgré le prompt : appliquer le repli du spec — déplacer le code des recettes dans des composants CodeExec figés du canvas et faire router l'Agent vers eux (même canvas, recette hors de portée du LLM).

- [ ] **Step 4: Remplir les « attendus » de DEMO.md avec les vrais chiffres + question libre testée**

Compléter chaque `<à remplir>` avec les chiffres réels constatés. Tester au moins 2 questions libres plausibles du client (ex. « combien de pièces ont été usinées par mois ? », « quelle est la plage de température de l'atelier ? ») et noter les réponses.

- [ ] **Step 5: Commit final + mise à jour du spec si des décisions ont bougé**

```bash
git add poc/famat/DEMO.md docs/superpowers/specs/2026-08-11-famat-drift-agent-poc-design.md
git commit -m "docs(famat-poc): déroulé de démo validé, chiffres de référence"
```
