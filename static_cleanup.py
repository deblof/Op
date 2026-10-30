"""Bounded, literal-only cleanup and string discovery. Never evaluate Lua calls."""
import math
from deobfuscator import Parser, Unsupported
from lua_values import literal
from source_spans import SourceParser
from vendor.unprom import ast_nodes as A

MAX_SOURCE_BYTES = 200_000
MAX_REPORT_BYTES = 500_000


def pure(node):
    if isinstance(node, A.Number):
        value = float(node.value)
        if not math.isfinite(value) or abs(value) > 2 ** 53 or (value == 0 and node.raw.startswith('-')):
            raise ValueError()
        return value
    if isinstance(node, A.String) and node.raw is not None:
        return Parser(node.raw).string()
    if isinstance(node, A.Paren):
        return pure(node.inner)
    if isinstance(node, A.UnOp):
        value = pure(node.operand)
        if node.op == '-' and type(value) is float and value != 0:
            return -value
        if node.op == '#' and isinstance(value, bytes):
            return float(len(value))
        raise ValueError()
    if isinstance(node, A.BinOp):
        left, right = pure(node.left), pure(node.right)
        if node.op == '..' and isinstance(left, bytes) and isinstance(right, bytes):
            if len(left) + len(right) > MAX_SOURCE_BYTES:
                raise ValueError()
            return left + right
        if type(left) is not float or type(right) is not float:
            raise ValueError()
        if node.op == '+': value = left + right
        elif node.op == '-': value = left - right
        elif node.op == '*': value = left * right
        elif node.op == '/' and right != 0: value = left / right
        else: raise ValueError()
        # Avoid number formatting/rounding and signed-zero differences.
        if not math.isfinite(value) or value == 0 or abs(value) > 2 ** 53:
            raise ValueError()
        return value
    raise ValueError()


def source_value(value):
    if isinstance(value, bytes):
        return literal(value)
    if value.is_integer():
        return '(' + str(int(value)) + ')'
    return '(' + repr(value) + ')'


def function_shape(node):
    """An alpha-renamed AST shape, used only to label XOR candidates."""
    names = {}
    def shape(value):
        if isinstance(value, A.Name):
            if value.name in ('string', 'table', 'bit32'):
                return ('global', value.name)
            return ('name', names.setdefault(value.name, len(names)))
        if isinstance(value, A.Node):
            return (value.kind, tuple((key, shape(item)) for key, item in A.iter_fields(value)
                                     if key not in ('raw', 'binding')))
        if isinstance(value, (list, tuple)):
            return tuple(shape(x) for x in value)
        return value
    return shape(node)


_XOR_HELPER = """return function(b,c)local d={}local e=#c;for f=1,#b do
local g=string.byte(b,f)local h=string.byte(c,(f-1)%e+1)
d[f]=string.char(bit32.bxor(g,h))end;return table.concat(d)end"""
_XOR_SHAPE = function_shape(SourceParser(_XOR_HELPER).parse_chunk().body.stmts[0].values[0])


def cleanup(source):
    if len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
        return None
    parser = SourceParser(source)
    tree = parser.parse_chunk()
    edits = []
    has_xor_helper = any(isinstance(n, A.Function) and function_shape(n) == _XOR_SHAPE for n in A.walk(tree))
    for start, end, node in parser.expressions:
        if not isinstance(node, (A.BinOp, A.UnOp, A.Paren)):
            continue
        try:
            value = pure(node)
        except (ValueError, Unsupported, OverflowError, RecursionError):
            continue
        before = source[start:end]
        after = source_value(value)
        if before != after and '--' not in before:
            edits.append((start, end, after))
    # String tokens are decoded using the byte parser, never Python's Unicode
    # interpretation of Lua decimal escapes.
    strings, seen, report_size = [], set(), 0
    candidates = []
    xor_candidates = []
    for token in parser.toks:
        if token.type != 'STRING' or token.raw is None:
            continue
        try:
            value = Parser(token.raw).string()
        except Unsupported:
            continue
        item = literal(value)
        if item not in seen and len(strings) < 5000 and report_size + len(item) < MAX_REPORT_BYTES:
            seen.add(item)
            strings.append(item)
            report_size += len(item)
        if token.raw != item:
            start = parser.offset(token)
            edits.append((start, start + len(token.raw), item))
    # Builtin-looking calls are useful candidates, but may be shadowed. They
    # are reported separately and are never substituted into executable code.
    for start, end, node in parser.expressions:
        if not isinstance(node, A.Call) or len(candidates) >= 1000:
            continue
        fragment = source[start:end]
        if has_xor_helper and len(node.args) == 2 and len(xor_candidates) < 1000:
            try:
                data, key = pure(node.args[0]), pure(node.args[1])
                if isinstance(data, bytes) and isinstance(key, bytes) and key and len(data) < 65536:
                    item = literal(bytes(b ^ key[i % len(key)] for i, b in enumerate(data)))
                    if item not in seen and report_size + len(item) < MAX_REPORT_BYTES:
                        seen.add(item)
                        xor_candidates.append(item)
                        report_size += len(item)
            except (ValueError, Unsupported, RecursionError):
                pass
        try:
            reader = Parser(fragment)
            value = reader.expression()
            reader.space()
            if reader.pos == len(fragment):
                item = literal(value)
                if item not in seen and report_size + len(item) < MAX_REPORT_BYTES:
                    seen.add(item)
                    candidates.append(item)
                    report_size += len(item)
        except (Unsupported, ValueError, RecursionError):
            pass
    edits.sort(key=lambda edit: (edit[0], -edit[1]))
    selected, end = [], -1
    for edit in edits:
        if edit[0] >= end:
            selected.append(edit)
            end = edit[1]
    result = source
    for start, end, value in reversed(selected):
        result = result[:start] + value + result[end:]
    if len(result.encode('utf-8')) > MAX_SOURCE_BYTES * 4:
        return None
    # Format by inserting whitespace at parsed statement/block boundaries.
    # Original comments and syntax remain in place; no AST printer is used.
    formatted = SourceParser(result)
    formatted.parse_chunk()
    for offset, depth in sorted(formatted.layout.items(), reverse=True):
        if offset < len(result):
            result = result[:offset] + '\n' + '    ' * min(depth, 64) + result[offset:]
    # Ensure inserted whitespace did not alter tokenization (e.g. a comment).
    check = SourceParser(result)
    check.parse_chunk()
    identity = lambda p: [(t.type, t.raw if t.raw is not None else t.value) for t in p.toks]
    if identity(check) != identity(formatted) or len(result.encode('utf-8')) > MAX_SOURCE_BYTES * 4:
        return None
    return {'source': result.strip() + '\n', 'changes': len(selected), 'strings': strings,
            'candidates': candidates, 'xor_candidates': xor_candidates, 'functions': sum(isinstance(n, A.Function) for n in A.walk(tree))}
