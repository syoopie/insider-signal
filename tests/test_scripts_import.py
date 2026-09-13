"""
Every entrypoint in scripts/ and research/scripts/ must import cleanly.

`backfill_sic.py` sat broken for months because nothing ever loaded it: the
store.py move could have done the same to any of these. Importing is not
running, so this catches bad imports and module-level typos, not logic. It must
pass with no DATABASE_URL in the environment, which is what CI has.
"""
from pathlib import Path

import pytest

from tests.conftest import RESEARCH_SCRIPTS, SCRIPTS, load_script

ENTRYPOINTS = sorted([*SCRIPTS.glob("*.py"), *RESEARCH_SCRIPTS.glob("*.py")])


def test_the_glob_finds_both_directories():
    assert {p.parent for p in ENTRYPOINTS} == {SCRIPTS, RESEARCH_SCRIPTS}


@pytest.mark.parametrize("script", ENTRYPOINTS, ids=lambda p: p.stem)
def test_script_imports(script: Path):
    load_script(script)
