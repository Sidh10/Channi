"""audit() pipeline: schema-shaped outputs and ground-truth isolation (SYNTHETIC data)."""

import json

import numpy as np
import pandas as pd
import pytest

from chhanni_audit.pipeline import DETECTION_LOG_COLUMNS, SYNTHETIC_NOTICE, audit, detection_view
from chhanni_audit.scoring import CHECKS
from chhanni_audit.tamper import TamperSpec, inject, inject_many


@pytest.fixture(scope="module")
def tampered(demo_dataset):
    """Three wards tampered with three different kinds (labels set on those rows only)."""
    specs = [
        TamperSpec(kind="round", ward_code="SYN-W03", fraction=0.05),
        TamperSpec(kind="fabricate", ward_code="SYN-W12", fraction=0.4),
        TamperSpec(kind="fabricate", ward_code="SYN-W20", mode="add", n_entries=400, dates=("2026-07-30",)),
    ]
    logs, _ = inject_many(demo_dataset.logs, specs, rng=99)
    return logs


def _fingerprint(res):
    """Everything the audit decides, as comparable plain data."""
    return json.dumps({"scores": res.ward_score_records(), "flags": res.flag_records(), "summary": res.run_summary()}, sort_keys=True)


def test_detection_view_drops_ground_truth(tampered):
    view = detection_view(tampered)
    assert "is_injected_tamper" not in view.columns
    assert set(view.columns) <= set(DETECTION_LOG_COLUMNS)


def test_ground_truth_isolation(demo_dataset, tampered):
    """Detection must give identical results whatever the label column holds, or if it is absent."""
    base = _fingerprint(audit(demo_dataset.wards, tampered))
    rng = np.random.default_rng(0)
    variants = {
        "dropped": tampered.drop(columns=["is_injected_tamper"]),
        "shuffled": tampered.assign(is_injected_tamper=rng.permutation(tampered["is_injected_tamper"].to_numpy())),
        "inverted": tampered.assign(is_injected_tamper=~tampered["is_injected_tamper"]),
        "all_true": tampered.assign(is_injected_tamper=True),
        "garbage": tampered.assign(is_injected_tamper="not a label"),
    }
    for name, logs in variants.items():
        assert _fingerprint(audit(demo_dataset.wards, logs)) == base, name


def test_row_order_does_not_matter(demo_dataset, tampered):
    shuffled = tampered.sample(frac=1.0, random_state=1).reset_index(drop=True)
    assert _fingerprint(audit(demo_dataset.wards, shuffled)) == _fingerprint(audit(demo_dataset.wards, tampered))


def test_tampered_wards_flagged_and_ranked_lowest(demo_dataset, tampered):
    res = audit(demo_dataset.wards, tampered)
    summary = res.run_summary()
    assert set(summary["flagged_wards"]) == {"SYN-W03", "SYN-W12", "SYN-W20"}
    assert "SYN-W03" in summary["flagged_by_check"]["roundness"]
    assert "SYN-W12" in summary["flagged_by_check"]["benford"]
    assert "SYN-W20" in summary["flagged_by_check"]["baseline_daily"]
    ranked = sorted(res.ward_score_records(), key=lambda r: r["confidence_score"])
    assert {r["ward_code"] for r in ranked[:3]} == {"SYN-W03", "SYN-W12", "SYN-W20"}


def test_ward_score_records_match_schema(demo_dataset, tampered):
    res = audit(demo_dataset.wards, tampered)
    recs = res.ward_score_records()
    assert len(recs) == len(demo_dataset.wards)
    for r in recs:
        assert set(r) == {"ward_code", "confidence_score", "flagged", "metrics"}
        assert 0.0 <= r["confidence_score"] <= 1.0 and round(r["confidence_score"], 4) == r["confidence_score"]
        assert isinstance(r["flagged"], bool)
        json.dumps(r["metrics"], allow_nan=False)  # JSONB-safe
        assert r["metrics"]["data_notice"] == SYNTHETIC_NOTICE
        assert r["flagged"] == (r["confidence_score"] <= 0.01)


def test_flag_records_match_schema(demo_dataset, tampered):
    res = audit(demo_dataset.wards, tampered)
    flags = res.flag_records()
    entry_ids = set(tampered["entry_id"])
    assert flags
    for f in flags:
        assert set(f) == {"ward_code", "check_type", "log_entry_ref", "period_start", "period_end", "score", "details"}
        assert f["check_type"] in CHECKS
        assert f["log_entry_ref"] is None or f["log_entry_ref"] in entry_ids
        pd.Timestamp(f["period_start"]), pd.Timestamp(f["period_end"])
        assert f["details"]["level"] in ("ward", "ward_day", "entry")
        json.dumps(f["details"], allow_nan=False)
    ward_level = [f for f in flags if f["details"]["level"] == "ward"]
    assert {f["ward_code"] for f in ward_level} == {"SYN-W03", "SYN-W12", "SYN-W20"}
    assert all(f["log_entry_ref"] is None for f in ward_level)
    assert any(f["details"]["level"] == "entry" for f in flags)  # roundness localisation


def test_run_params_and_summary_are_json(demo_dataset):
    res = audit(demo_dataset.wards, demo_dataset.logs, localise=False)
    json.dumps(res.run_params(), allow_nan=False)
    json.dumps(res.run_summary(), allow_nan=False)
    assert res.run_summary()["data_notice"] == SYNTHETIC_NOTICE
    assert res.run_params()["n_days"] == 28


def test_accepts_db_like_input(demo_dataset):
    """Phase 4 will pass object-dtype strings and python dates from Postgres rows."""
    logs = demo_dataset.logs.copy()
    db_like = logs.assign(
        ward_code=logs["ward_code"].astype(str).astype(object), entry_date=logs["entry_date"].dt.date
    ).rename(columns={"entry_id": "entry_id"})
    a = audit(demo_dataset.wards, logs, localise=False).ward_table
    b = audit(demo_dataset.wards, db_like, localise=False).ward_table
    pd.testing.assert_frame_equal(a, b)


def test_unknown_ward_in_log_rejected(demo_dataset):
    bad = demo_dataset.logs.head(10).copy()
    bad["ward_code"] = "NOT-A-WARD"
    with pytest.raises(ValueError):
        audit(demo_dataset.wards, bad)


def test_period_window_restricts_entries(demo_dataset):
    res = audit(demo_dataset.wards, demo_dataset.logs, period_start="2026-07-06", period_end="2026-07-19", localise=False)
    assert res.n_days == 14
    in_window = (demo_dataset.logs["entry_date"] <= pd.Timestamp("2026-07-19")).sum()
    assert res.n_entries.sum() == in_window
