"""Filesystem locations for the audit engine.

The data directory comes from the AUDIT_ENGINE_DATA_DIR environment variable.
Relative values resolve against the repository root (NOT the current working
directory), matching .env.example. Default: <repo>/data.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

PACKAGE_DIR = Path(__file__).resolve().parent
AUDIT_ENGINE_DIR = PACKAGE_DIR.parent
REPO_ROOT = AUDIT_ENGINE_DIR.parent

DATA_DIR_ENV_VAR = "AUDIT_ENGINE_DATA_DIR"
SYNTHETIC_PREFIX = "synthetic_"


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the data directory (absolute)."""
    source = os.environ if env is None else env
    raw = (source.get(DATA_DIR_ENV_VAR) or "").strip()
    path = Path(raw) if raw else Path("data")
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def synthetic_dir(env: Mapping[str, str] | None = None) -> Path:
    return data_dir(env) / "synthetic"


def synthetic_output_path(filename: str, env: Mapping[str, str] | None = None) -> Path:
    """Path for a synthetic output file; refuses names without the synthetic_ prefix."""
    if not filename.startswith(SYNTHETIC_PREFIX):
        raise ValueError(
            f"synthetic outputs must be named '{SYNTHETIC_PREFIX}*' so they can't be "
            f"mistaken for real data (got {filename!r})"
        )
    if Path(filename).name != filename:
        raise ValueError(f"expected a bare filename, got {filename!r}")
    directory = synthetic_dir(env)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename
