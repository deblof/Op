"""Recognize a narrow static byte-addition helper without executing Lua."""

import re

from deobfuscator import MAX_INPUT_BYTES, Parser, PROTECTED, RESERVED, Unsupported

# Limit matching to the start of the file; all identifiers must have the same
# roles throughout the helper. Other helper variants are intentionally ignored.
_HEADER = re.compile(r'''\A\s*(?:--[^\r\n]*[\r\n]+\s*)*
    local\s+(?P<prefix>[A-Za-z_]\w*)\s*=\s*"(?:\\.|[^"\\])*"\s*
    local\s+function\s+(?P<fn>[A-Za-z_]\w*)\s*\(\s*(?P<a>[A-Za-z_]\w*)\s*,\s*(?P<b>[A-Za-z_]\w*)\s*\)
    \s*local\s+(?P<t>[A-Za-z_]\w*)\s*=\s*\{\s*\}\s*
    for\s+(?P<i>[A-Za-z_]\w*)\s*=\s*1\s*,\s*\#(?P=a)\s+do\s*
    (?P=t)\s*\[\s*(?P=i)\s*\]\s*=\s*string\.char\s*\(\s*\(\s*
    string\.byte\s*\(\s*(?P=a)\s*,\s*(?P=i)\s*\)\s*\+\s*
    string\.byte\s*\(\s*(?P=b)\s*,\s*\(\s*\(\s*(?P=i)\s*%\s*\#(?P=b)\s*\)\s*\)\s*\+\s*1\s*\)\s*
    \)\s*%\s*256\s*\)\s*end\s+return\s+table\.concat\s*\(\s*(?P=t)\s*\)\s*end\s*;\s*
    local\s+(?P<list>[A-Za-z_]\w*)\s*=\s*\{''', re.VERBOSE | re.ASCII)


def _literal(data: bytes) -> str:
    return '"' + ''.join(chr(b) if 32 <= b <= 126 and b not in (34, 92)
                         else f'\\{b:03d}' for b in data) + '"'


def decode_addition_table(source: str) -> tuple[str, int]:
    """Fold an immediately following table of literal calls; never execute code."""
    if len(source.encode('utf-8')) > MAX_INPUT_BYTES:
        return source, 0
    header = source[:16384]
    match = _HEADER.match(header)
    if not match:
        return source, 0
    names = [match.group(name) for name in ('prefix', 'fn', 'a', 'b', 't', 'i', 'list')]
    if len(set(names)) != len(names) or any(n in PROTECTED or n in RESERVED for n in names):
        return source, 0
    parser = Parser(header)
    parser.pos = match.end()
    replacements = []
    try:
        while len(replacements) < 128:
            parser.space()
            start = parser.pos
            if not parser.keyword(match.group('fn')):
                raise Unsupported()
            parser.require('(')
            a = parser.string()
            parser.require(',')
            b = parser.string()
            parser.require(')')
            if not b or len(a) > 4096 or len(b) > 4096:
                raise Unsupported()
            decoded = bytes((value + b[(i + 1) % len(b)]) % 256 for i, value in enumerate(a))
            replacements.append((start, parser.pos, _literal(decoded)))
            if not parser.take(','):
                parser.require('}')
                break
        else:
            return source, 0
    except Unsupported:
        return source, 0
    result = source
    for start, end, value in reversed(replacements):
        result = result[:start] + value + result[end:]
    if len(result.encode('utf-8')) > MAX_INPUT_BYTES:
        return source, 0
    return result, len(replacements)
