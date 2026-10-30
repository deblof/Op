"""Isolated static analysis, with hard time and memory limits."""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
    resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024, 192 * 1024 * 1024))
except (ImportError, OSError, ValueError):
    sys.exit(2)
from static_cleanup import cleanup, MAX_SOURCE_BYTES
try:
    raw = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
    result = None if len(raw) > MAX_SOURCE_BYTES else cleanup(raw.decode('utf-8'))
    print(json.dumps(result, ensure_ascii=True))
except (ValueError, SyntaxError, RecursionError, MemoryError, OverflowError, TypeError):
    print('null')
