"""SYNTHETIC generator: determinism, calibration target, schema shapes."""

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from chhanni_audit.benford import benford_conformity
from chhanni_audit.calibration import CRDT_DELHI_GENERATED_TPD
from chhanni_audit.synthetic import LOG_COLUMNS, WARD_COLUMNS, SyntheticParams, describe, generate_dataset


def test_calibration_constant_is_the_verified_crdt_figure():
    assert CRDT_DELHI_GENERATED_TPD == 11_862.0
    assert SyntheticParams().city_daily_total_t == 11_862.0


def test_deterministic_for_seed():
    a, b = generate_dataset(7), generate_dataset(7)
    pdt.assert_frame_equal(a.wards, b.wards)
    pdt.assert_frame_equal(a.logs, b.logs)


def test_different_seeds_differ():
    a, b = generate_dataset(7), generate_dataset(8)
    assert not a.logs["tonnage"].equals(b.logs["tonnage"])


@pytest.mark.parametrize("seed", [2026, 0, 123])
def test_hits_city_daily_total(seed):
    ds = generate_dataset(seed)
    daily = ds.logs.groupby("entry_date", observed=True)["tonnage"].sum()
    assert len(daily) == ds.params.n_days
    assert daily.mean() == pytest.approx(11_862.0, abs=0.5)  # only 3-decimal rounding error


def test_ward_schema(demo_dataset):
    w = demo_dataset.wards
    assert tuple(w.columns) == WARD_COLUMNS
    assert w["ward_code"].is_unique
    assert (w["population"] > 0).all() and w["population"].dtype.kind == "i"
    for col in ("income_proxy", "sanitation_proxy"):
        assert w[col].between(0, 1).all()
        assert np.allclose(w[col], w[col].round(4))  # fits NUMERIC(6,4)
    assert (w["data_origin"] == "synthetic").all()
    assert w["ward_code"].str.startswith("SYN-").all()


def test_log_schema(demo_dataset):
    logs = demo_dataset.logs
    assert tuple(logs.columns) == LOG_COLUMNS
    assert logs["entry_id"].is_unique
    assert (logs["tonnage"] >= 0).all()
    kg = logs["tonnage"].to_numpy() * 1000
    assert np.allclose(kg, np.round(kg))  # 3 decimal places
    assert (logs["data_origin"] == "synthetic").all()
    assert not logs["is_injected_tamper"].any()
    assert set(logs["ward_code"].astype(str)) <= set(demo_dataset.wards["ward_code"])
    assert pd.api.types.is_datetime64_any_dtype(logs["entry_date"])


def test_clean_entries_conform_by_construction(demo_dataset):
    assert demo_dataset.params.exactly_benford
    assert benford_conformity(demo_dataset.logs["tonnage"].to_numpy()).verdict == "conforms"


def test_daily_totals_are_not_benford_applicable(demo_dataset):
    logs = demo_dataset.logs
    one = logs[logs["ward_code"] == "SYN-W01"].groupby("entry_date", observed=True)["tonnage"].sum()
    assert benford_conformity(one.to_numpy()).verdict == "not_applicable"


def test_describe_flags_per_capita_as_artefact(demo_dataset):
    d = describe(demo_dataset)
    assert d["data_origin"] == "synthetic"
    assert "implied_per_capita_kg_per_day_ARTEFACT" in d
    assert d["trip_load_span_decades_p5_p95"] > 1.0
