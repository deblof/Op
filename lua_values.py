"""Represent Lua values without changing binary strings or IEEE double values."""
import math


def literal(value):
    if value is None:
        return 'nil'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, bytes):
        return '"' + ''.join(chr(b) if 32 <= b < 127 and b not in (34, 92)
                             else f'\\{b:03d}' for b in value) + '"'
    if isinstance(value, (float, int)):
        if math.isnan(value):
            return '(0/0)'
        if math.isinf(value):
            return '(1/0)' if value > 0 else '(-1/0)'
        return repr(value)
    raise ValueError('Unsupported Lua value')
