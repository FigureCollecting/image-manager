"""Regression test for the alembic.ini interpolation crash.

alembic.ini previously had ``sqlalchemy.url = %(DATABASE_URL)s``, which
ConfigParser reads as an interpolation token looking for a literal
``database_url`` key in the same file (NOT the OS environment variable of
that name). Any alembic command that reads the [alembic] section --
including plain ``alembic current`` -- crashed with
``InterpolationMissingOptionError`` before env.py ever got a chance to set
sqlalchemy.url from the environment.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_alembic_current_does_not_crash_on_config_parse() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "config_parse_check.db"
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
        result = subprocess.run(
            ["alembic", "current"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined = result.stdout + result.stderr
        assert "InterpolationMissingOptionError" not in combined
        assert result.returncode == 0, f"alembic current failed:\n{combined}"
