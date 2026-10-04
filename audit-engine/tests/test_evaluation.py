"""Evaluation harness and report script: reproducibility, labelling, paths."""

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from chhanni_audit.evaluate import CLEAN, Scenario, aggregate, run_tasks
from chhanni_audit.paths import REPO_ROOT, data_dir, synthetic_output_path
from chhanni_audit.pipeline import AuditConfig
from chhanni_audit.run_detection import BANNER, dumps, to_json_safe
from chhanni_audit.synthetic import SyntheticParams

SCENARIOS = (CLEAN, Scenario(kind="round", fraction=0.02), Scenario(kind="fabricate", fraction=0.4, n_targets=2))


def _tasks(seeds=(0, 1)):
    return [("t", s, SyntheticParams(), SCENARIOS, AuditConfig()) for s in seeds]


def test_evaluation_is_deterministic_and_worker_independent():
    serial_a = run_tasks(_tasks())
    serial_b = run_tasks(_tasks())
    with ProcessPoolExecutor(max_workers=2) as ex:
        parallel = run_tasks(_tasks(), ex)
    assert dumps(serial_a) == dumps(serial_b) == dumps(parallel)


def test_aggregate_reports_fpr_next_to_tpr():
    outcomes = run_tasks(_tasks())
    agg = aggregate(outcomes, SCENARIOS[1], "t")
    for k in ("benford", "roundness", "baseline", "baseline_daily", "any"):
        assert {"tpr", "fpr", "runs_with_any_false_flag"} <= set(agg["checks"][k])
        assert set(agg["checks"][k]["tpr"]) == {"mean", "min", "max"}
    assert agg["n_seeds"] == 2
    assert agg["checks"]["roundness"]["tpr"]["mean"] == 1.0  # 2% rounded entries are caught


def test_json_has_no_nan():
    text = dumps({"a": float("nan"), "b": [1.0, float("inf")]})
    assert "NaN" not in text and "Infinity" not in text
    assert to_json_safe({"x": float("nan")}) == {"x": None}


def test_banner_says_synthetic():
    assert "SYNTHETIC" in BANNER


def test_data_dir_relative_to_repo_root_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert data_dir({}) == (REPO_ROOT / "data").resolve()
    assert data_dir({"AUDIT_ENGINE_DATA_DIR": "./data"}) == (REPO_ROOT / "data").resolve()
    assert data_dir({"AUDIT_ENGINE_DATA_DIR": "other/place"}) == (REPO_ROOT / "other/place").resolve()
    absolute = tmp_path / "abs"
    assert data_dir({"AUDIT_ENGINE_DATA_DIR": str(absolute)}) == absolute.resolve()


def test_synthetic_output_names_must_be_prefixed(tmp_path):
    env = {"AUDIT_ENGINE_DATA_DIR": str(tmp_path)}
    with pytest.raises(ValueError):
        synthetic_output_path("report.json", env)
    p = synthetic_output_path("synthetic_x.json", env)
    assert p.parent == Path(tmp_path) / "synthetic" and p.name == "synthetic_x.json"
