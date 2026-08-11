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
