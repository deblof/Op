"""Bound concurrency and isolate parsing of untrusted Lua."""
import json
from pathlib import Path
import subprocess
import sys
import threading
from static_cleanup import MAX_SOURCE_BYTES
_BUSY = threading.BoundedSemaphore(1)


def analyze_source(source):
    if len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
        return None
    if not _BUSY.acquire(blocking=False):
        return 'busy'
    try:
        try:
            result = subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('cleanup_worker.py'))],
                                    input=source.encode('utf-8'), capture_output=True, env={}, timeout=6)
            if result.returncode or len(result.stdout) > 6_000_000:
                return None
            data = json.loads(result.stdout)
            if not isinstance(data, dict) or not isinstance(data.get('source'), str):
                return None
            return data
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
    finally:
        _BUSY.release()
