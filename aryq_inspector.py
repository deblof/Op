"""Inspect the exact supplied Aryq sample as data, without executing Lua.

This decoder is deliberately fingerprinted to a single script: its keys and
anti-tamper behavior are specific to that script and may vary with runtime.
"""
from dataclasses import dataclass
import hashlib
import re
import threading

from deobfuscator import Parser, Unsupported

_SAMPLE_SHA256 = '21e56392c256c364678ef6801158718f56ba40f8c21967a40d760b10e5a195fc'
_BLOCKS = 482
_MAX_REPORT = 950_000
_LOCK = threading.BoundedSemaphore(1)
_INITIAL = re.compile(r',\(\(function\(\) local a=(\d+)\s*')
_STEP = re.compile(r'a=\(a\*(\d+)\+\((unfnC|m0XTmwd\[1\]:byte\([123]\))\)\*(\d+)\+(\d+)\)%2147483647\s*')
_END = re.compile(r'return a\+\((-?\d+)\) end\)\(\)\),(\d+)\)')


@dataclass(frozen=True)
class AryqReport:
    text: str
    instruction_blocks: int
    integers: int
    constant_blocks: int
    strings: int
    numbers: int
    truncated: bool
    unpacked: str
    readable: str | None = None
    structured: int = 0
    translated_instructions: int = 0


def _seed(source, pos):
    match = _INITIAL.match(source, pos)
    if not match:
        raise ValueError('Unsupported key')
    value, pos = int(match.group(1)), match.end()
    steps = 0
    while match := _STEP.match(source, pos):
        ref = match.group(2)
        byte = 0 if ref == 'unfnC' else b'\x8d|y'[int(ref[-2]) - 1]
        value = (value * int(match.group(1)) + byte * int(match.group(3)) + int(match.group(4))) % 2147483647
        pos = match.end()
        steps += 1
        if steps > 100:
            raise ValueError('Too many key operations')
    match = _END.match(source, pos)
    if not match or not steps:
        raise ValueError('Unsupported key ending')
    return value + int(match.group(1)), int(match.group(2)), match.end()


def _decrypt(raw, key, offset):
    checksum = sum((i + 1) * value for i, value in enumerate(raw)) % 2147483647
    first = (key + checksum - offset) % 2147483646 or 2147483646
    second = ((key + checksum - offset) * 40503 + 1013904223) % 2147483646 or 2147483646
    output = bytearray()
    for value in raw:
        first = first * 16807 % 2147483647
        second = second * 48271 % 2147483647
        product = (first % 65536) * (second % 32768)
        output.append((value - (product + product // 256 + product // 65536)) % 256)
    return bytes(output)


def _varint(data, pos):
    value = 0
    for shift in range(0, 70, 7):
        if pos == len(data):
            break
        byte = data[pos]
        pos += 1
        value += (byte % 128) << shift
        if byte < 128:
            return value, pos
    raise ValueError('Invalid variable-length integer')


def _constants(data):
    pos = 0
    index = 0
    while pos < len(data):
        kind = data[pos]
        length, pos = _varint(data, pos + 1)
        if length > len(data) - pos:
            raise ValueError('Truncated constant')
        value = data[pos:pos + length]
        pos += length
        index += 1
        if kind == 2:
            yield 'number', value
        else:
            yield 'string', bytes((byte - (195 * index + 147 * n + 244 * index * n)) % 256
                                  for n, byte in enumerate(value, 1))


def inspect_aryq(source):
    """Return a bounded report, 'busy', or None for other input."""
    if hashlib.sha256(source.encode('utf-8')).hexdigest() != _SAMPLE_SHA256:
        return None
    if not _LOCK.acquire(blocking=False):
        return 'busy'
    try:
        return _inspect(source)
    except (ValueError, Unsupported, IndexError, OverflowError):
        # An invalid stream must not be reported as decoded.
        return None
    finally:
        _LOCK.release()


def _inspect(source):
    lines = ['Static analysis of the exact supplied Aryq sample.',
             'Assumes its anti-tamper value is zero, as in an ordinary runtime.',
             'Other environments can change the keys. Strings are candidate constants;',
             'they are not proof of execution. This is not reconstructed Lua.', '']
    size = sum(len(line) + 1 for line in lines)
    truncated = False
    counts = {'number': 0, 'string': 0}
    integers = 0
    blocks = []
    replacements = []
    streams, pools = [], []
    for name in ('l00oIo', 'J47b09niogn'):
        matches = list(re.finditer(r'\b' + name + r'\(', source))[1:]
        if len(matches) != _BLOCKS:
            raise ValueError('Unexpected block count')
        blocks.append(len(matches))
        for number, match in enumerate(matches, 1):
            parser = Parser(source)
            parser.pos = match.end()
            raw = parser.string()
            key, offset, end = _seed(source, parser.pos)
            data = _decrypt(raw, key, offset)
            if name == 'l00oIo':
                pos = 0
                values = []
                while pos < len(data):
                    value, pos = _varint(data, pos)
                    values.append(str(value))
                    integers += 1
                streams.append([int(value) for value in values])
                replacements.append((match.start(), end, '{' + ','.join(values) + '}'))
            else:
                values = []
                pool = list(_constants(data))
                pools.append(pool)
                for index, (kind, value) in enumerate(pool, 1):
                    literal = '"' + ''.join(chr(byte) if 32 <= byte <= 126 and byte not in (34, 92)
                                           else f'\\{byte:03d}' for byte in value) + '"'
                    values.append('tonumber(' + literal + ')' if kind == 'number' else literal)
                    counts[kind] += 1
                    line = f'pool {number}, constant {index}, {kind}: {value!r}'
                    if size + len(line) + 1 <= _MAX_REPORT - 100:
                        lines.append(line)
                        size += len(line) + 1
                    else:
                        truncated = True
                replacements.append((match.start(), end, '{' + ','.join(values) + '}'))
    if truncated:
        lines.append('Report truncated; counts include all decoded constants.')
    pieces = []
    position = 0
    for start, end, replacement in sorted(replacements):
        pieces.extend((source[position:start], replacement))
        position = end
    pieces.append(source[position:])
    unpacked = ''.join(pieces)
    if len(unpacked.encode('utf-8')) > 8_000_000:
        raise ValueError('Unpacked output too large')
    readable, structured, translated = None, 0, 0
    try:
        from aryq_lifter import translate
        from lua_ast_source import expr
        from vendor.unprom.parser import Parser as LuaParser
        from vendor.unprom.lexer import tokenize
        marker = 'return slotYi(_Pgmf26Z[1],stackN3j[1],'
        start = source.index(marker) + len(marker)
        end = source.index('},...);elseif nodexc3Ox', start) + 1
        captures = LuaParser(tokenize('return ' + source[start:end])).parse_chunk().body.stmts[0].values[0]
        readable, structured, translated = translate(streams, pools, expr(captures))
    except (ValueError, TypeError, IndexError, OverflowError, SyntaxError, RecursionError):
        # Keep the existing report if a script cannot be completely translated.
        pass
    return AryqReport('\n'.join(lines) + '\n', blocks[0], integers, blocks[1],
                      counts['string'], counts['number'], truncated, unpacked,
                      readable, structured, translated)
