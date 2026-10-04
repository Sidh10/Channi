"""scripts/real_data_benford_cpcb.py: fixed primary spec, repeat counting, determinism.

The repeat-counting test uses a tiny SYNTHETIC frame. The determinism test reads the REAL
public CSV (read-only) and is skipped if it is absent."""

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from chhanni_audit.paths import data_dir

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "real_data_benford_cpcb.py"
REAL_CSV = data_dir() / "public" / "cpcb_msw_statewise_tpd.csv"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("real_data_benford_cpcb", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_primary_analysis_is_fixed(script):
    assert script.PRIMARY["report_year"] == "2021-22"
    assert script.PRIMARY["metric"] == "generated"
    assert script.PRIMARY["alpha"] == 0.01
    assert "REAL PUBLIC DATA" in script.LABEL


def test_exact_repeats_on_synthetic_frame(script):
    rows = []
    for year, a, b in [("2019-20", 10.0, 5.0), ("2020-21", 10.0, 6.0), ("2021-22", 11.0, 6.0)]:
        rows += [
            {"report_year": year, "state": "SYN-A", "metric": "generated", "raw_cell": str(a), "value_tpd": a, "parse_status": "clean"},
            {"report_year": year, "state": "SYN-B", "metric": "generated", "raw_cell": str(b), "value_tpd": b, "parse_status": "clean"},
        ]
    df = pd.DataFrame(rows).assign(state_canonical=lambda d: d["state"])
    r = script.exact_repeats(df)
    assert r["n_comparable_pairs"] == 4
    assert r["n_exact_repeats"] == 2  # SYN-A 19-20 -> 20-21 and SYN-B 20-21 -> 21-22
    assert {(x["state"], tuple(x["years"])) for x in r["repeats"]} == {("SYN-A", ("2019-20", "2020-21")), ("SYN-B", ("2020-21", "2021-22"))}


@pytest.mark.skipif(not REAL_CSV.exists(), reason="real CPCB CSV not present")
def test_real_analysis_is_deterministic_and_primary_first(script):
    a = script.analyse(REAL_CSV)
    b = script.analyse(REAL_CSV)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["all_tests"][0]["role"] == "primary (fixed a priori)"
    assert a["primary_analysis"]["result"]["selection"]["report_year"] == "2021-22"
    assert "REAL" in a["data_label"]
