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
