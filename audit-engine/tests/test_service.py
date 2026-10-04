"""chhanni_audit.service: the JSON stdin/stdout contract the Node backend relies on.

All data here is SYNTHETIC. CLI tests run the real subprocess (`python -m chhanni_audit.service`)
on a small dataset; validation edge cases run in-process through `service.run_command`, which is
the same code path minus stdin/stdout."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chhanni_audit import __version__, service
from chhanni_audit.evaluate import Scenario, add_count, apply_scenario
from chhanni_audit.paths import synthetic_dir
from chhanni_audit.pipeline import SYNTHETIC_NOTICE, audit
from chhanni_audit.run_detection import to_json_safe
from chhanni_audit.scoring import CHECKS
from chhanni_audit.synthetic import SyntheticParams, generate_dataset

ENGINE_DIR = Path(__file__).resolve().parents[1]
SMALL_SEED = 7
SMALL_PARAMS = {"n_wards": 8, "n_days": 7, "city_daily_total_t": 1000.0}
AUDIT_KEYS = ("data_notice", "engine_version", "params", "summary", "ward_scores", "flags")
WARD_FIELDS = ("ward_code", "population", "income_proxy", "sanitation_proxy", "data_origin")
INJECTED = {"SYN-W02": ("round", "spread", 0.10), "SYN-W05": ("fabricate", "last_day", 0.30)}


def cli(command, payload=None, raw=None, args=None):
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    argv = [command] if args is None else args
    proc = subprocess.run(
        [sys.executable, "-m", "chhanni_audit.service", *argv], input=data, capture_output=True, cwd=ENGINE_DIR, timeout=300
    )
    return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")


def cli_json(command, payload):
    code, out, err = cli(command, payload)
    assert code == 0, err
    return json.loads(out)


def direct_frames(ward_rows, log_rows):
    """DataFrames for a direct pipeline.audit() call, built with plain pandas (not service parsing)."""
    w = pd.DataFrame(ward_rows)[list(WARD_FIELDS)]
    lg = pd.DataFrame(log_rows)[["entry_id", "ward_code", "entry_date", "tonnage", "data_origin"]]
    lg = lg.assign(entry_id=lg["entry_id"].astype("int64"), entry_date=pd.to_datetime(lg["entry_date"], format="%Y-%m-%d"))
    return w, lg


# --- fixtures: a small SYNTHETIC city, loaded the way Node loads it --------------------------------


@pytest.fixture(scope="module")
def small_raw():
    code, out, err = cli("generate", {"seed": SMALL_SEED, "params": SMALL_PARAMS})
    assert code == 0, err
    return out


@pytest.fixture(scope="module")
def small_gen(small_raw):
    return json.loads(small_raw)


@pytest.fixture(scope="module")
def small_wards(small_gen):
    return [{k: w[k] for k in WARD_FIELDS} for w in small_gen["wards"]]


@pytest.fixture(scope="module")
def small_logs(small_gen):
    """Generated rows with DB-style ids: node-pg returns BIGSERIAL ids as strings."""
    return [dict(r, entry_id=str(i + 1)) for i, r in enumerate(small_gen["logs"])]


def ward_rows(logs, ward):
    return [{k: r[k] for k in ("entry_id", "ward_code", "entry_date", "tonnage")} for r in logs if r["ward_code"] == ward]


@pytest.fixture(scope="module")
def tampered_logs(small_logs):
    """Two wards get phantom entries via the CLI (appended with new ids, as Node's INSERT would)."""
    logs = list(small_logs)
    for ward, (kind, placement, fraction) in INJECTED.items():
        out = cli_json(
            "inject",
            {"seed": SMALL_SEED, "ward_code": ward, "kind": kind, "placement": placement, "fraction": fraction, "logs": ward_rows(logs, ward)},
        )
        n = len(logs)
        logs += [
            {"entry_id": str(n + j + 1), "ward_code": e["ward_code"], "entry_date": e["entry_date"], "tonnage": e["tonnage"], "data_origin": e["data_origin"]}
            for j, e in enumerate(out["entries"])
        ]
    return logs


@pytest.fixture(scope="module")
def tampered_audit(small_wards, tampered_logs):
    return cli_json("audit", {"wards": small_wards, "logs": tampered_logs, "period_start": None, "period_end": None})


# --- version -----------------------------------------------------------------------------------------


def test_version_cli():
    for raw in (b"{}", b""):
        code, out, err = cli("version", raw=raw)
        assert code == 0, err
        assert json.loads(out) == {"engine_version": __version__, "commands": ["audit", "generate", "inject", "version"]}
        assert "chhanni_audit.service version: ok" in err  # diagnostics on stderr, not stdout


# --- generate ----------------------------------------------------------------------------------------


def test_generate_cli_is_deterministic_and_matches_generator(small_raw, small_gen):
    code, again, err = cli("generate", {"seed": SMALL_SEED, "params": SMALL_PARAMS})
    assert code == 0, err
    assert again == small_raw  # byte-identical

    ds = generate_dataset(SMALL_SEED, replace(SyntheticParams(), **SMALL_PARAMS))
    assert small_gen["seed"] == SMALL_SEED
    assert small_gen["params"] == to_json_safe(ds.params.to_dict())
    assert "SYNTHETIC" in small_gen["data_notice"] and "OVERRIDDEN" in small_gen["data_notice"]
    assert [w["ward_code"] for w in small_gen["wards"]] == ds.wards["ward_code"].tolist()
    assert [w["population"] for w in small_gen["wards"]] == ds.wards["population"].tolist()
    assert [w["income_proxy"] for w in small_gen["wards"]] == ds.wards["income_proxy"].tolist()
    logs = small_gen["logs"]
    assert set(logs[0]) == {"ward_code", "entry_date", "tonnage", "data_origin"}  # no ground truth, no ids
    assert [r["ward_code"] for r in logs] == ds.logs["ward_code"].astype(str).tolist()
    assert [r["entry_date"] for r in logs] == ds.logs["entry_date"].dt.strftime("%Y-%m-%d").tolist()
    assert [r["tonnage"] for r in logs] == ds.logs["tonnage"].tolist()
    assert {r["data_origin"] for r in logs} | {w["data_origin"] for w in small_gen["wards"]} == {"synthetic"}


def test_generate_default_is_demo_seed_2026():
    code, out, err = cli("generate", {})
    assert code == 0, err
    expected = service.encode(service.generate_payload(2026, SyntheticParams())) + b"\n"
    assert out == expected
    data = json.loads(out)
    assert data["seed"] == 2026 and data["params"] == to_json_safe(SyntheticParams().to_dict())
    assert "SYNTHETIC" in data["data_notice"] and "11,862 TPD" in data["data_notice"]
    assert "OVERRIDDEN" not in data["data_notice"]

    # ...and equals the demo dataset already written by run_detection (skip if not generated).
    wards_csv = synthetic_dir() / "synthetic_wards_seed2026.csv"
    logs_csv = synthetic_dir() / "synthetic_ward_tonnage_logs_seed2026.csv"
    if not (wards_csv.exists() and logs_csv.exists()):
        pytest.skip("seed-2026 CSVs not present (run python -m chhanni_audit.run_detection)")
    w = pd.read_csv(wards_csv)
    assert [r["ward_code"] for r in data["wards"]] == w["ward_code"].tolist()
    assert [r["population"] for r in data["wards"]] == w["population"].tolist()
    assert [r["sanitation_proxy"] for r in data["wards"]] == w["sanitation_proxy"].tolist()
    lg = pd.read_csv(logs_csv, dtype={"ward_code": str, "entry_date": str})
    assert len(data["logs"]) == len(lg)
    assert [r["ward_code"] for r in data["logs"]] == lg["ward_code"].tolist()
    assert [r["entry_date"] for r in data["logs"]] == lg["entry_date"].tolist()
    assert [r["tonnage"] for r in data["logs"]] == lg["tonnage"].tolist()
    assert lg["entry_id"].tolist() == list(range(1, len(lg) + 1))  # so DB ids 1..N line up with the CSV


# --- inject ------------------------------------------------------------------------------------------


def test_inject_cli_returns_only_new_rows_for_target(small_logs):
    target = "SYN-W03"
    own = ward_rows(small_logs, target)
    own_dates = {r["entry_date"] for r in own}
    last_day = max(r["entry_date"] for r in small_logs)

    out = cli_json("inject", {"seed": 11, "ward_code": target, "kind": "round", "placement": "spread", "logs": own})
    entries = out["entries"]
    assert "SYNTHETIC" in out["data_notice"]
    assert out["sizing"]["fraction_source"] == "default"
    assert out["sizing"]["fraction"] == service.DEFAULT_INJECT_FRACTIONS[("round", "spread")] == 0.015
    assert len(entries) == out["sizing"]["n_added"] == add_count(0.015, len(own))
    assert out["report"]["modified_entry_ids"] == [] and "added_entry_ids" not in out["report"]
    for e in entries:
        assert set(e) == {"ward_code", "entry_date", "tonnage", "data_origin", "is_injected_tamper"}  # no entry_id
        assert e["ward_code"] == target and e["entry_date"] in own_dates
        assert e["data_origin"] == "synthetic" and e["is_injected_tamper"] is True
        v = e["tonnage"]  # rounded to 1 significant figure
        assert v > 0 and float(f"{v:.1g}") == v

    # Sending the whole log (all wards) gives the same rows: only the target ward is touched.
    all_rows = [{k: r[k] for k in ("entry_id", "ward_code", "entry_date", "tonnage")} for r in small_logs]
    same = cli_json("inject", {"seed": 11, "ward_code": target, "kind": "round", "placement": "spread", "logs": all_rows})
    assert same["entries"] == entries

    fab = cli_json("inject", {"seed": 11, "ward_code": target, "kind": "fabricate", "placement": "last_day", "fraction": 0.2, "logs": own})
    lo, hi = fab["report"]["fabricate_range_used_t"]
    assert fab["sizing"]["fraction_source"] == "request" and fab["sizing"]["default_fraction"] == 0.075
    assert len(fab["entries"]) == add_count(0.2, len(own))
    assert {e["entry_date"] for e in fab["entries"]} == {last_day}
    assert all(lo <= e["tonnage"] <= hi and e["ward_code"] == target for e in fab["entries"])


def test_inject_never_modifies_input_and_ignores_row_order(small_logs):
    df = service.logs_frame(small_logs, require_origin=False)
    before = df.copy()
    a = service.inject_entries(df, 5, "SYN-W04", "fabricate", "spread", 0.1)
    pd.testing.assert_frame_equal(df, before)
    shuffled = df.sample(frac=1.0, random_state=3).reset_index(drop=True)
    b = service.inject_entries(shuffled, 5, "SYN-W04", "fabricate", "spread", 0.1)
    assert a == b
    c = service.inject_entries(df, 6, "SYN-W04", "fabricate", "spread", 0.1)
    assert c["entries"] != a["entries"]  # the seed matters


def test_inject_window_bounds_set_size_and_last_day(small_logs):
    own = ward_rows(small_logs, "SYN-W03")
    out = service.run_command(
        "inject",
        {"seed": 2, "ward_code": "SYN-W03", "kind": "round", "placement": "last_day", "logs": own, "period_start": "2026-07-07", "period_end": "2026-07-09"},
    )
    n_window = sum("2026-07-07" <= r["entry_date"] <= "2026-07-09" for r in own)
    assert out["sizing"]["target_ward_entries"] == n_window < len(own)
    assert out["sizing"]["n_added"] == len(out["entries"]) == add_count(0.015, n_window)
    assert {e["entry_date"] for e in out["entries"]} == {"2026-07-09"}


def test_inject_defaults_match_heldout_table():
    path = synthetic_dir() / "synthetic_detection_report.json"
    if not path.exists():
        pytest.skip("detection report not present (run python -m chhanni_audit.run_detection)")
    sizing = json.loads(path.read_text(encoding="utf-8"))["demo_sizing"]
    assert set(service.DEFAULT_INJECT_FRACTIONS) == {(k, p) for k in service.INJECT_KINDS for p in service.INJECT_PLACEMENTS}
    for (kind, placement), f in service.DEFAULT_INJECT_FRACTIONS.items():
        name = f"{kind}|add|{placement}"
        group = sizing["groups"][name]
        assert f in group["holdout_sizes"], name
        assert f >= sizing["recommendations"][name]["size"], name
        held = next(h for h in group["holdout"] if h["scenario"]["fraction"] == f)
        assert held["checks"]["any"]["tpr"]["mean"] >= sizing["reliability_threshold"], name


def test_inject_reproduces_detection_report_demo_runs(demo_dataset):
    """Demo-config check (seed 2026, target SYN-W27): each default injection equals the one the
    detection report pre-checked in section [9], and the audit flags SYN-W27 and nothing else."""
    ds, target = demo_dataset, "SYN-W27"
    path = synthetic_dir() / "synthetic_detection_report.json"
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    for (kind, placement), f in service.DEFAULT_INJECT_FRACTIONS.items():
        got = service.inject_entries(ds.logs, 2026, target, kind, placement)
        logs, _ = apply_scenario(ds, Scenario(kind=kind, mode="add", fraction=f, placement=placement, n_targets=1), [target])
        added = logs[logs["is_injected_tamper"]]
        assert [e["entry_date"] for e in got["entries"]] == added["entry_date"].dt.strftime("%Y-%m-%d").tolist()
        assert [e["tonnage"] for e in got["entries"]] == added["tonnage"].tolist()
        res = audit(ds.wards, logs)
        assert res.run_summary()["flagged_wards"] == [target], (kind, placement)
        if report is not None:
            row = next(r for r in report["default_seed_demo_runs"]["groups"][f"{kind}|add|{placement}"] if r["size"] == f)
            conf = float(res.ward_table.set_index("ward_code").loc[target, "confidence"])
            assert float(f"{conf:.6g}") == row["target_confidence"], (kind, placement)


# --- audit -------------------------------------------------------------------------------------------


def test_audit_cli_equals_direct_audit(small_wards, tampered_logs, tampered_audit):
    w, lg = direct_frames(small_wards, tampered_logs)
    direct = audit(w, lg)
    expected = to_json_safe(direct.to_dict())
    for k in AUDIT_KEYS:
        assert tampered_audit[k] == expected[k], k
    assert tampered_audit["data_notice"] == SYNTHETIC_NOTICE
    assert tampered_audit["db"]["ward_scores"] == to_json_safe(direct.ward_score_records())
    assert tampered_audit["db"]["run"]["summary"] == expected["summary"]
    assert tampered_audit["summary"]["flagged_wards"] == sorted(INJECTED)  # both injected wards caught


def test_db_section_rules(tampered_audit, tampered_logs):
    db = tampered_audit["db"]
    run = db["run"]
    dates = [r["entry_date"] for r in tampered_logs]
    assert (run["period_start"], run["period_end"]) == (min(dates), max(dates))
    assert run["engine_version"] == __version__ and run["params"]["config"]["alpha"] == 0.01
    assert set(run) == {"engine_version", "params", "summary", "period_start", "period_end"}

    scores = db["ward_scores"]
    assert len(scores) == len({r["ward_code"] for r in tampered_logs})
    for s in scores:
        assert set(s) == {"ward_code", "confidence_score", "flagged", "metrics"}
        assert 0.0 <= s["confidence_score"] <= 1.0

    flagged = {s["ward_code"] for s in scores if s["flagged"]}
    flags = db["flags"]
    assert flagged and {f["ward_code"] for f in flags} == flagged  # every flagged ward has >= 1 flag, and only those
    ward_level_rows = [f for f in tampered_audit["flags"] if f["details"]["level"] == "ward"]
    assert len(flags) == len(ward_level_rows) < len(tampered_audit["flags"])  # hints were folded in, not added
    ids = {int(r["entry_id"]) for r in tampered_logs}
    kinds = set()
    for f in flags:
        assert set(f) == {"ward_code", "check_type", "period_start", "period_end", "score", "details"}
        assert f["check_type"] in CHECKS and f["details"]["level"] == "ward"
        assert f["period_start"] == run["period_start"] and f["period_end"] == run["period_end"]
        assert f["score"] == f["details"]["q_value"] <= 0.01 and f["details"]["p_value"] is not None
        hints = f["details"]["localisation_hints"]
        if f["check_type"] == "roundness":
            assert hints["level"] == "entry" and hints["is_flag"] is False and hints["n"] == len(hints["entries"]) > 0
            assert {h["entry_id"] for h in hints["entries"]} <= ids
            kinds.add("entry")
        elif f["check_type"] == "baseline_daily":
            assert hints["level"] == "ward_day" and hints["n"] == len(hints["days"]) > 0
            assert all(run["period_start"] <= d["date"] <= run["period_end"] for d in hints["days"])
            kinds.add("ward_day")
        else:
            assert hints is None
    assert kinds == {"entry", "ward_day"}  # both hint kinds exercised by the fixture


def test_audit_ignores_is_injected_tamper(small_wards, tampered_logs, tampered_audit):
    rng = np.random.default_rng(0)
    labelled = [dict(r, is_injected_tamper=bool(b)) for r, b in zip(tampered_logs, rng.integers(0, 2, len(tampered_logs)))]
    labelled[0]["is_injected_tamper"] = "garbage"
    out = cli_json("audit", {"wards": small_wards, "logs": labelled})
    assert out == tampered_audit


def test_audit_accepts_db_shaped_values_in_any_order(small_wards, tampered_logs, tampered_audit):
    """node-pg may send BIGINT/NUMERIC as strings; row order must not matter."""
    as_strings = [dict(r, tonnage=f"{r['tonnage']:.3f}") for r in tampered_logs]
    ints = [dict(r, entry_id=int(r["entry_id"])) for r in tampered_logs]
    rng = np.random.default_rng(1)
    shuffled = [tampered_logs[i] for i in rng.permutation(len(tampered_logs))]
    wards_str = [dict(w, income_proxy=str(w["income_proxy"])) for w in small_wards]
    for variant in (as_strings, ints, shuffled):
        out = service.run_command("audit", {"wards": small_wards, "logs": variant})
        assert to_json_safe(out["db"]) == tampered_audit["db"]
    out = service.run_command("audit", {"wards": wards_str, "logs": tampered_logs})
    assert to_json_safe(out["db"]["flags"]) == tampered_audit["db"]["flags"]


def test_audit_window_is_the_dates_actually_audited(small_wards, small_logs):
    base = service.run_command("audit", {"wards": small_wards, "logs": small_logs})
    wide = service.run_command("audit", {"wards": small_wards, "logs": small_logs, "period_start": "2026-07-01", "period_end": "2026-07-31"})
    assert (wide["db"]["run"]["period_start"], wide["db"]["run"]["period_end"]) == ("2026-07-06", "2026-07-12")
    assert wide["ward_scores"] == base["ward_scores"] and wide["params"] == base["params"]
    assert wide["db"]["run"]["params"]["requested_period_start"] == "2026-07-01"

    narrow = service.run_command("audit", {"wards": small_wards, "logs": small_logs, "period_start": "2026-07-07", "period_end": "2026-07-10"})
    run = narrow["db"]["run"]
    assert (run["period_start"], run["period_end"]) == ("2026-07-07", "2026-07-10")
    in_window = sum("2026-07-07" <= r["entry_date"] <= "2026-07-10" for r in small_logs)
    assert run["params"]["n_log_rows_in_window"] == in_window and run["summary"]["n_entries_audited"] == in_window
    w, lg = direct_frames(small_wards, small_logs)
    direct = to_json_safe(audit(w, lg, period_start="2026-07-07", period_end="2026-07-10").to_dict())
    for k in ("params", "ward_scores", "flags"):
        assert narrow[k] == direct[k], k

    with pytest.raises(service.InvalidInput, match="no log entries"):
        service.run_command("audit", {"wards": small_wards, "logs": small_logs, "period_start": "2026-09-01"})


# --- errors ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["not_json", "timestamp_date", "unknown_ward_inject", "inflate", "unknown_command", "unknown_key", "no_command"],
)
def test_bad_input_cli_gives_nonzero_exit_and_json_error(case, small_wards, small_logs):
    own = ward_rows(small_logs, "SYN-W01")[:50]
    audit_logs = small_logs[:200]
    cases = {
        "not_json": dict(command="audit", raw=b"{not json"),
        "timestamp_date": dict(
            command="audit", payload={"wards": small_wards, "logs": [dict(audit_logs[0], entry_date="2026-07-06T00:00:00.000Z")]}
        ),
        "unknown_ward_inject": dict(command="inject", payload={"seed": 1, "ward_code": "SYN-W99", "kind": "round", "logs": own}),
        "inflate": dict(command="inject", payload={"seed": 1, "ward_code": "SYN-W01", "kind": "inflate", "logs": own}),
        "unknown_command": dict(command="score", payload={}),
        "unknown_key": dict(command="audit", payload={"wards": small_wards, "logs": audit_logs, "period_strat": "2026-07-06"}),
        "no_command": dict(command=None, payload={}, args=[]),
    }
    c = cases[case]
    code, out, err = cli(c["command"], c.get("payload"), raw=c.get("raw"), args=c.get("args"))
    assert code != 0
    body = json.loads(out)
    assert isinstance(body["error"], str) and body["error"] and body["error_type"] in ("invalid_input", "internal")
    assert "error" in err


def _small_payloads(small_wards, small_logs):
    logs = small_logs[:300]
    own = ward_rows(small_logs, "SYN-W01")
    return logs, own


@pytest.mark.parametrize(
    "command,mutate,exc",
    [
        ("generate", lambda w, l, o: {"params": {"n_wardz": 8}}, service.InvalidInput),
        ("generate", lambda w, l, o: {"params": {"n_wards": "8"}}, service.InvalidInput),
        ("generate", lambda w, l, o: {"params": {"n_wards": 3}}, service.InvalidInput),
        ("generate", lambda w, l, o: {"params": {"start_date": "06/07/2026"}}, service.InvalidInput),
        ("generate", lambda w, l, o: {"seed": -1}, service.InvalidInput),
        ("generate", lambda w, l, o: {"seed": True}, service.InvalidInput),
        ("inject", lambda w, l, o: {"seed": 1, "ward_code": "SYN-W01", "kind": "round", "fraction": 0, "logs": o}, service.InvalidInput),
        ("inject", lambda w, l, o: {"seed": 1, "ward_code": "SYN-W01", "kind": "round", "fraction": 1.5, "logs": o}, service.InvalidInput),
        ("inject", lambda w, l, o: {"seed": 1, "ward_code": "SYN-W01", "kind": "round", "fraction": True, "logs": o}, service.InvalidInput),
        ("inject", lambda w, l, o: {"seed": 1, "ward_code": "SYN-W01", "kind": "round", "placement": "middle", "logs": o}, service.InvalidInput),
        ("inject", lambda w, l, o: {"ward_code": "SYN-W01", "kind": "round", "logs": o}, service.InvalidInput),
        ("inject", lambda w, l, o: {"seed": 1, "ward_code": "SYN-W01", "kind": "round", "logs": [dict(r, entry_id="x") for r in o]}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w, "logs": []}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w, "logs": l + l[:1]}, service.InvalidInput),  # duplicate entry_id
        ("audit", lambda w, l, o: {"wards": w, "logs": [dict(l[0], tonnage=-1)] + l[1:]}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w, "logs": [dict(l[0], tonnage="abc")] + l[1:]}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w, "logs": [dict(l[0], tonnage=True)] + l[1:]}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w, "logs": [{k: v for k, v in l[0].items() if k != "data_origin"}] + l[1:]}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w, "logs": [dict(l[0], entry_date="2026-02-30")] + l[1:]}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": [dict(w[0], population=None)] + w[1:], "logs": l}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w + w[:1], "logs": l}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w, "logs": l, "config": {"alpha": 0.5, "nope": 1}}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w, "logs": l, "config": {"multiple_testing": "none"}}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w, "logs": l, "config": {"benford_reference": "other"}}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w, "logs": l, "period_start": "2026-07-10", "period_end": "2026-07-08"}, service.InvalidInput),
        ("audit", lambda w, l, o: {"wards": w[1:], "logs": l}, ValueError),  # log references a ward not sent
    ],
)
def test_bad_input_rejected_in_process(command, mutate, exc, small_wards, small_logs):
    logs, own = _small_payloads(small_wards, small_logs)
    with pytest.raises(exc):
        service.run_command(command, mutate(small_wards, logs, own))


def test_parse_request_rejects_non_finite_and_non_objects():
    for raw in (b'{"a": NaN}', b'{"a": Infinity}', b"[1, 2]", b"", b"\xef\xbb\xbf"):
        with pytest.raises(service.InvalidInput):
            service.parse_request(raw)
    assert service.parse_request(b"\xef\xbb\xbf{}") == {}  # tolerate a UTF-8 BOM
    assert service.parse_request(b"", allow_empty=True) == {}


def test_config_overrides_are_applied_and_recorded(small_wards, small_logs):
    out = service.run_command("audit", {"wards": small_wards, "logs": small_logs, "config": {"benford_reference": "peer"}})
    assert out["db"]["run"]["params"]["config"]["benford_reference"] == "peer"
    assert all(s["metrics"]["benford"]["reference"] == "peer" for s in out["db"]["ward_scores"])
