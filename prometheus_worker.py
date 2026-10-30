"""Run bounded static parsing in a separate process; never execute input Lua."""

import json
import os
import sys

# Isolated Python excludes the script directory, so add only this trusted path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
except (ImportError, OSError, ValueError):
    # Do not process untrusted input if resource limits cannot be enforced.
    sys.exit(2)

from prometheus_transform import MAX_SOURCE_BYTES, flatten_dispatcher

try:
    raw = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        sys.exit(2)
    result = flatten_dispatcher(raw.decode('utf-8'))
    if result is None:
        print('null')
    else:
        source, blocks = result
        print(json.dumps({'source': source, 'blocks': blocks}, ensure_ascii=True))
except (ValueError, RecursionError, MemoryError, OverflowError, SyntaxError):
    print('null')
