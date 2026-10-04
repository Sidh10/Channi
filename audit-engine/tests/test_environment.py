"""Phase 0 smoke test: the package imports and the scientific stack is available."""

import chhanni_audit


def test_package_imports():
    assert chhanni_audit.__version__


def test_scientific_stack_available():
    import numpy, pandas, scipy.stats, sklearn  # noqa: F401
