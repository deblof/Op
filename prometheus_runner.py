"""Limit and isolate experimental static dispatcher analysis."""

import json
from pathlib import Path
import subprocess
import sys
import threading

from prometheus_transform import MAX_SOURCE_BYTES

_WORKER = Path(__file__).with_name('prometheus_worker.py')
_BUSY = threading.BoundedSemaphore(1)


def analyze_dispatcher(source: str):
    """Return (source, blocks), None when unrecognized, or 'busy'."""
    size = len(source.encode('utf-8'))
    if (size < 1000 or size > MAX_SOURCE_BYTES or 'while' not in source
            or 'function' not in source):
        return None
    if not _BUSY.acquire(blocking=False):
        return 'busy'
    try:
        try:
            result = subprocess.run([sys.executable, '-I', str(_WORKER)],
                                    input=source.encode('utf-8'), capture_output=True,
                                    timeout=6, env={}, check=False)
            if result.returncode != 0 or len(result.stdout) > MAX_SOURCE_BYTES * 4:
                return None
            data = json.loads(result.stdout)
            if (not isinstance(data, dict) or not isinstance(data.get('source'), str)
                    or not isinstance(data.get('blocks'), int) or not 3 <= data['blocks'] <= 512):
                return None
            return data['source'], data['blocks']
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None
    finally:
        _BUSY.release()
