"""
Every entrypoint in scripts/ must import cleanly.

`backfill_sic.py` sat broken for months because nothing ever loaded it: the
store.py move could have done the same to any of these. Importing is not
running, so this catches bad imports and module-level typos, not logic. It must
pass with no DATABASE_URL in the environment, which is what CI has.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPTS = sorted(p for p in (Path(__file__).parent.parent / "scripts").glob("*.py"))


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_script_imports(script: Path):
    spec = importlib.util.spec_from_file_location(f"_import_check_{script.stem}", script)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
