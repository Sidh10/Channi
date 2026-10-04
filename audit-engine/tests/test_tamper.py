"""SYNTHETIC tampering injector: only modifies what it reports modifying."""

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from chhanni_audit.tamper import TamperSpec, inflate_values, inject, round_sig_figs, round_to_step

WARD = "SYN-W05"


def _check_only_reported_rows_changed(before: pd.DataFrame, after: pd.DataFrame, report):
    touched = set(report.modified_entry_ids) | set(report.added_entry_ids)
    assert len(touched) == report.n_tampered
    # Untouched rows are identical in every column.
    b = before.set_index("entry_id")
    a = after.set_index("entry_id")
    untouched = b.index.difference(list(touched))
    pdt.assert_frame_equal(a.loc[untouched], b.loc[untouched])
    # Ground-truth label is True exactly on the reported rows.
    assert set(a.index[a["is_injected_tamper"]]) == touched
    # Added rows are new ids; modified rows keep ward/date.
    assert set(report.added_entry_ids).isdisjoint(b.index)
    for eid in report.modified_entry_ids:
        assert a.loc[eid, "ward_code"] == b.loc[eid, "ward_code"] == WARD
        assert a.loc[eid, "entry_date"] == b.loc[eid, "entry_date"]
    for eid in report.added_entry_ids:
        assert a.loc[eid, "ward_code"] == WARD and a.loc[eid, "data_origin"] == "synthetic"


@pytest.mark.parametrize(
    "spec",
    [
        TamperSpec(kind="round", ward_code=WARD, fraction=0.1),
        TamperSpec(kind="round", ward_code=WARD, n_entries=25, round_step_t=1.0),
        TamperSpec(kind="fabricate", ward_code=WARD, fraction=0.2),
        TamperSpec(kind="inflate", ward_code=WARD, fraction=0.5, inflate_factor=1.3),
        TamperSpec(kind="round", ward_code=WARD, mode="add", n_entries=40),
        TamperSpec(kind="fabricate", ward_code=WARD, mode="add", n_entries=60, dates=("2026-08-02",)),
    ],
)
def test_injector_only_modifies_what_it_reports(demo_dataset, spec):
    before = demo_dataset.logs
    snapshot = before.copy()
    after, report = inject(before, spec, rng=42)
    pdt.assert_frame_equal(before, snapshot)  # input not mutated
    _check_only_reported_rows_changed(before, after, report)
    if spec.mode == "add":
        assert len(after) == len(before) + report.n_tampered
    else:
        assert len(after) == len(before)


def test_injector_is_deterministic(demo_dataset):
    spec = TamperSpec(kind="fabricate", ward_code=WARD, fraction=0.1)
    a, ra = inject(demo_dataset.logs, spec, rng=[1, 2])
    b, rb = inject(demo_dataset.logs, spec, rng=[1, 2])
    pdt.assert_frame_equal(a, b)
    assert ra.to_dict() == rb.to_dict()


def test_add_on_dates_places_entries_on_those_dates(demo_dataset):
    spec = TamperSpec(kind="fabricate", ward_code=WARD, mode="add", n_entries=30, dates=("2026-08-02",))
    after, rep = inject(demo_dataset.logs, spec, rng=3)
    added = after[after["entry_id"].isin(rep.added_entry_ids)]
    assert (added["entry_date"] == pd.Timestamp("2026-08-02")).all()


def test_fabricate_values_within_ward_p10_p90(demo_dataset):
    after, rep = inject(demo_dataset.logs, TamperSpec(kind="fabricate", ward_code=WARD, fraction=0.3), rng=4)
    lo, hi = rep.fabricate_range_used_t
    vals = after.loc[after["entry_id"].isin(rep.modified_entry_ids), "tonnage"]
    assert vals.between(lo - 0.001, hi + 0.001).all()


def test_inflate_multiplies_by_factor(demo_dataset):
    after, rep = inject(demo_dataset.logs, TamperSpec(kind="inflate", ward_code=WARD, fraction=1.0, inflate_factor=2.0), rng=5)
    assert rep.overreport_ratio == pytest.approx(2.0, abs=1e-4)
    b = demo_dataset.logs.set_index("entry_id").loc[rep.modified_entry_ids, "tonnage"]
    a = after.set_index("entry_id").loc[rep.modified_entry_ids, "tonnage"]
    assert np.allclose(a, 2 * b, atol=0.0015)


def test_inflate_add_mode_rejected():
    with pytest.raises(ValueError):
        TamperSpec(kind="inflate", ward_code=WARD, mode="add", n_entries=5)


def test_unknown_ward_rejected(demo_dataset):
    with pytest.raises(ValueError):
        inject(demo_dataset.logs, TamperSpec(kind="round", ward_code="NOPE", fraction=0.1), rng=0)


def test_value_transforms():
    assert list(round_sig_figs(np.array([4.837, 0.287, 12.4, 0.0265]), 1)) == [5.0, 0.3, 10.0, 0.03]
    assert list(round_to_step(np.array([0.2, 1.4, 2.6]), 1.0)) == [1.0, 1.0, 3.0]
    rng = np.random.default_rng(0)
    kg = (inflate_values(np.array([1.001, 2.003]), 2.0, rng) * 1000).round()
    assert np.allclose(kg / 1000, [2.002, 4.006], atol=0.0015)
    assert list(inflate_values(np.array([1.001]), 2.0, dequantize=False)) == [2.002]
