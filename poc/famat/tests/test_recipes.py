import os
import pytest
import tempfile

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


def _mk(seq, serial, value, minute):
    # minute peut dépasser 59 (grandes séries synthétiques) -> déborde sur l'heure.
    hour, minute = 10 + minute // 60, minute % 60
    return (seq, serial, 10, "CORRECTION_X", value, f"2026-01-01 {hour:02d}:{minute:02d}:00")


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


def test_backtest_detects_upcoming_crossing():
    # 250 pièces stables autour de 0 puis dérive douce +0.0005/pièce sur 15
    # pièces : le franchissement de l'UCL final doit être anticipé AVANT
    # d'arriver (dérive trop brutale = premier point déjà hors limites =
    # inanticipable).
    #
    # NOTE ajustement (cf. brief Step 4) : avec seulement 30 pièces stables
    # (valeur initiale du brief), aucune pente dans [0.0003, 0.001] ni
    # aucune longueur de queue jusqu'à 200 points ne produit de vrai
    # franchissement — c'est structurel, pas un problème de réglage fin :
    # pour une rampe purement linéaire, mean_all + k_sigma*sigma_all croît
    # *plus vite* que le max de la rampe elle-même (sigma d'une suite
    # linéaire ~ amplitude/sqrt(12)), donc un contrôle Shewhart statique
    # calculé sur ses propres données ne "voit" jamais une tendance
    # linéaire pure, quels que soient la pente ou la longueur — c'est la
    # limite connue des cartes de contrôle statiques face à une dérive
    # lente. Il faut un historique stable nettement plus long pour que
    # sigma_all reste ancré près du bruit de fond pendant que la queue de
    # dérive devient un vrai outlier vis-à-vis des limites finales : d'où
    # 250 pièces stables (au lieu de 30). Pente et longueur de queue de
    # dérive inchangées (0.0005/pièce, 15 points, dans les bornes
    # suggérées par le brief).
    rows = [_mk(i + 1, f"P{i:02d}", 0.001 * ((i % 3) - 1), i) for i in range(250)]
    rows += [_mk(i + 251, f"Q{i:02d}", 0.0005 * (i + 1), i + 250) for i in range(15)]
    b = fr.backtest(fr.load_rows(rows), "CORRECTION_X", 10, jump_k=50.0)  # jump_k haut : pas de coupure de segment
    assert b["n_parts"] == 265
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
