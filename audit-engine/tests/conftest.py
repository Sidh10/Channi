"""Shared fixtures. All data here is SYNTHETIC."""

import pytest

from chhanni_audit.synthetic import generate_dataset

DEMO_SEED = 2026


@pytest.fixture(scope="session")
def demo_dataset():
    """The default demo-seed synthetic dataset (do not mutate; inject() returns copies)."""
    return generate_dataset(DEMO_SEED)
