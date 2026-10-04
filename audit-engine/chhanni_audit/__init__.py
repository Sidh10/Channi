"""Chhanni statistical audit engine (Benford's Law + socio-economic baseline).

All datasets this package generates are synthetic (docs/decisions.md D5) and
must be labeled as such wherever they are written or displayed.

Entry points:
* `chhanni_audit.pipeline.audit(wards_df, logs_df)` -> AuditResult (Phase 4 calls this)
* `chhanni_audit.benford.benford_conformity(values)` -> Benford verdict for ANY numeric series
* `python -m chhanni_audit.run_detection` -> reproducible multi-seed detection report
"""

__version__ = "0.1.0"
