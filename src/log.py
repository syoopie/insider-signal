"""Console logging for the entrypoints, teed to a timestamped file under logs/."""

import os
import sys
from datetime import datetime

# Windows consoles default to cp1252, which cannot encode the box-drawing chars
# in phase() or the accented company names that come back from EDGAR. Force
# UTF-8 on the real streams before anything writes to them.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self._streams:
            s.flush()


def setup_log_tee(script_name: str) -> str:
    """Redirect stdout/stderr to both console and a timestamped log file. Returns log path."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(_LOG_DIR, f"{script_name}_{ts}.log")
    log_file = open(log_path, "w", buffering=1, encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)
    return log_path


def log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def phase(title: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"\n[{ts}] {'─' * 10} {title} {'─' * 10}", flush=True)


def fmt_elapsed(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"
