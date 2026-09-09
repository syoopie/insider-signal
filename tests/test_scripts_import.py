"""
Every entrypoint in scripts/ must import cleanly.

`backfill_sic.py` sat broken for months because nothing ever loaded it: the
store.py move could have done the same to any of these. Importing is not
running, so this catches bad imports and module-level typos, not logic. It must
pass with no DATABASE_URL in the environment, which is what CI has.
"""
from pathlib import Path

import pytest

from tests.conftest import SCRIPTS, load_script

ENTRYPOINTS = sorted(SCRIPTS.glob("*.py"))


@pytest.mark.parametrize("script", ENTRYPOINTS, ids=lambda p: p.stem)
def test_script_imports(script: Path):
    load_script(script)
