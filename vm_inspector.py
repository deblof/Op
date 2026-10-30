"""Statically inspect one recognized Lua VM serialization; never run Lua."""

from dataclasses import dataclass
import hashlib
import struct

from deobfuscator import MAX_BYTES

# Exact interpreter fingerprint: opcode meanings are specific to this wrapper.
_INTERPRETER_HASH = '855aa1c9b352c6595f9a121a52f858ea2e09d32a8fdbd85c96b5822bc57cf515'
_MARKER = 'return v15("LOL!'
_SUFFIX = '",v9(),...);'
_MAX_FUNCTIONS = 256
_MAX_ITEMS = 20_000
_MAX_DEPTH = 64
_MAX_REPORT = 950_000
_MAX_DECRYPT_BYTES = 500_000
_DECODER_SIG = 'b763d433a05f9fe355cd0b30c8dfa7c87bbc77efe4e0bda6980ffa392bd7c150'
_ROOT_CONSTANTS = [b'string', b'char', b'byte', b'sub', b'bit32', b'bit', b'bxor', b'table', b'concat', b'insert']
_ROOT_SETUP = [
    (57, 0, 1, None, 2), (41, 0, 0, 2, 4), (57, 1, 1, None, 2), (41, 1, 1, 3, 4),
    (57, 2, 1, None, 2), (41, 2, 2, 4, 4), (57, 3, 5, None, 2), (47, 3, 10, 1, 0),
    (68, 0, 10, None, 0), (57, 3, 6, None, 2), (41, 4, 3, 7, 4), (57, 5, 8, None, 2),
    (41, 5, 5, 9, 4), (57, 6, 8, None, 2), (41, 6, 6, 10, 4), (136, 7, 0, 6, 0),
    (128, 0, 6, 0, 0), (128, 0, 0, 0, 0), (128, 0, 4, 0, 0), (128, 0, 1, 0, 0),
    (128, 0, 2, 0, 0), (128, 0, 5, 0, 0),
]
# Labels are derived from the recognized interpreter; unknown opcodes stay numeric.
_OP_LABELS = {
    9: 'LOAD_OPERAND', 10: 'CALL_FIXED_ARGS', 24: 'ADD_REGISTERS', 25: 'GET_TABLE_REGISTER',
    26: 'RETURN_REGISTER', 32: 'CALL_NO_ARGS', 33: 'LENGTH',
    34: 'SUBTRACT_REGISTERS', 41: 'GET_TABLE_KEY', 53: 'CALL_DISCARD',
    56: 'LOAD_BOOLEAN', 57: 'GET_GLOBAL', 60: 'CLOSURE',
    68: 'JUMP', 69: 'LOAD_OPERAND', 72: 'JUMP', 83: 'GET_UPVALUE',
    86: 'MOVE', 110: 'MODULO_CONSTANT', 113: 'NEW_TABLE', 119: 'CALL_TOP_ARGS',
    123: 'ADD_CONSTANT_REGISTER', 124: 'MODULO_REGISTERS', 130: 'PREPARE_METHOD',
    134: 'ADD_REGISTER_CONSTANT', 136: 'CLOSURE', 143: 'CALL_VARIABLE_RESULTS',
}


class InvalidVM(ValueError):
    """Unsupported or malformed virtualized input."""


@dataclass
class Function:
    params: int
    constants: list
    instructions: list
    children: list


@dataclass
class VMReport:
    text: str
    functions: int
    instructions: int
    constants: int
    truncated: bool
    recovered: int = 0


def unpack_payload(encoded: str) -> bytes:
    if len(encoded) % 2 or len(encoded) > MAX_BYTES:
        raise InvalidVM('Invalid payload length')
    output = bytearray()
    count = None
    for i in range(0, len(encoded), 2):
        pair = encoded[i:i + 2]
        if pair[1] == 'Q':
            if count is not None or pair[0] not in '0123456789':
                raise InvalidVM('Invalid run-length marker')
            count = int(pair[0])
        else:
            if any(c not in '0123456789ABCDEFabcdef' for c in pair):
                raise InvalidVM('Invalid hex byte')
            repeats = 1 if count is None else count
            if len(output) + repeats > MAX_BYTES:
                raise InvalidVM('Unpacked payload too large')
            output.extend(bytes([int(pair, 16)]) * repeats)
            count = None
    if count is not None:
        raise InvalidVM('Incomplete run-length marker')
    return bytes(output)


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.functions = 0
        self.instructions = 0
        self.constants = 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise InvalidVM('Truncated payload')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return int.from_bytes(self.take(2), 'little')

    def u32(self):
        return int.from_bytes(self.take(4), 'little')

    def function(self, depth=0):
        self.functions += 1
        if self.functions > _MAX_FUNCTIONS or depth > _MAX_DEPTH:
            raise InvalidVM('Too many functions or nested levels')
        count = self.u32()
        self.constants += count
        if self.constants > _MAX_ITEMS:
            raise InvalidVM('Too many constants')
        constants = []
        for _ in range(count):
            kind = self.u8()
            if kind == 0:
                value = None
            elif kind == 1:
                value = self.u8() != 0
            elif kind == 2:
                value = struct.unpack('<d', self.take(8))[0]
            elif kind == 3:
                value = self.take(self.u32())
            else:
                raise InvalidVM('Unknown constant type')
            constants.append(value)
        params = self.u8()
        count = self.u32()
        self.instructions += count
        if self.instructions > _MAX_ITEMS:
            raise InvalidVM('Too many instructions')
        instructions = []
        for _ in range(count):
            header = self.u8()
            if header & 1:
                instructions.append(None)
                continue
            mode = (header >> 1) & 3
            flags = (header >> 3) & 7
            opcode, a = self.u16(), self.u16()
            if mode == 0:
                b, c = self.u16(), self.u16()
            elif mode == 1:
                b, c = self.u32(), None
            elif mode == 2:
                b, c = self.u32() - 65536, None
            else:
                b, c = self.u32() - 65536, self.u16()
            for index, value in enumerate((a, b, c)):
                if (flags >> index) & 1 and (value is None or not 1 <= value <= len(constants)):
                    raise InvalidVM('Invalid constant reference')
            instructions.append((opcode, a, b, c, flags))
        count = self.u32()
        if count > _MAX_FUNCTIONS - self.functions:
            raise InvalidVM('Too many functions')
        children = [self.function(depth + 1) for _ in range(count)]
        return Function(params, constants, instructions, children)


def recover_strings(root):
    """Recover only literal XOR calls to the verified sample's decoder.

    This is pattern matching and byte arithmetic, not VM execution. Call sites
    are candidates: control flow and runtime mutation are not evaluated.
    """
    if (root.constants[:10] != _ROOT_CONSTANTS or root.instructions[:22] != _ROOT_SETUP
            or not root.children):
        return []
    decoder = root.children[0]
    signature = hashlib.sha256(repr((decoder.params, decoder.constants, decoder.instructions)).encode()).hexdigest()
    if signature != _DECODER_SIG or decoder.children:
        return []
    if any(inst and inst[1] == 7 for inst in root.instructions[22:]):
        return []
    recovered = []
    total_bytes = 0

    def visit(fn, path, registers, upvalues):
        nonlocal total_bytes
        # An upvalue written by the code is no longer a verified decoder reference.
        upvalues = upvalues - {inst[2] for inst in fn.instructions if inst and inst[0] in (30, 43)}
        for i in range(len(fn.instructions) - 3):
            move, first, second, call = fn.instructions[i:i + 4]
            if not all((move, first, second, call)):
                continue
            op, target, origin, _, flags = move
            if flags or not ((op == 86 and origin in registers) or (op == 83 and origin in upvalues)):
                continue
            if (first[0] != 9 or first[1] != target + 1 or first[4] != 2
                    or second[0] != 9 or second[1] != target + 2 or second[4] != 2
                    or call != (143, target, target + 2, 0, 0)):
                continue
            cipher, key = fn.constants[first[2] - 1], fn.constants[second[2] - 1]
            if not isinstance(cipher, bytes) or not isinstance(key, bytes) or not key:
                continue
            if total_bytes + len(cipher) > _MAX_DECRYPT_BYTES:
                return
            total_bytes += len(cipher)
            plaintext = bytes(byte ^ key[(position + 1) % len(key)] for position, byte in enumerate(cipher))
            recovered.append((path, i + 1, plaintext))
        # A child prototype may be instantiated multiple times. Only propagate
        # decoder upvalues that are verified at every closure site.
        child_sites = {}
        for i, inst in enumerate(fn.instructions):
            if not inst or inst[0] not in (60, 136):
                continue
            _, _, child, count, flags = inst
            if flags or not isinstance(child, int) or not 0 <= child < len(fn.children) or not isinstance(count, int) or count < 0:
                continue
            captures = fn.instructions[i + 1:i + 1 + count]
            if len(captures) != count or any(capture is None for capture in captures):
                child_sites.setdefault(child, []).append(set())
                continue
            found = {index for index, capture in enumerate(captures)
                     if (capture[0] == 128 and capture[2] in registers)
                     or (capture[0] != 128 and capture[2] in upvalues)}
            child_sites.setdefault(child, []).append(found)
        for child, sites in child_sites.items():
            verified = set.intersection(*sites)
            if verified:
                visit(fn.children[child], f'{path}.{child}', set(), verified)

    visit(root, '0', {7}, set())
    return recovered


def render(root, reader):
    recovered = recover_strings(root)
    lines = []
    size = 0
    truncated = False

    def add(line):
        nonlocal size, truncated
        if truncated:
            return
        remaining = _MAX_REPORT - size
        if len(line) + 1 > remaining:
            truncated = True
            return
        lines.append(line)
        size += len(line) + 1

    add('STATIC VM ANALYSIS — PARTIAL RECOVERY')
    add('This is a disassembly of virtual instructions, not reconstructed runnable Lua.')
    add('Some constants are still encrypted. No Lua code was executed.')
    add(f'Functions: {reader.functions}; instruction slots: {reader.instructions}; constants: {reader.constants}.')
    add('K indices refer to this function’s constants; opcodes are VM-specific.')
    add(f'Statically recovered XOR string candidates: {len(recovered)} (runtime behavior is not evaluated; recovery is capped at 500,000 bytes).')
    for path, pc, value in recovered:
        if truncated:
            break
        add(f'  FUNCTION {path}, instruction {pc:05d}: {value!r}')

    def visit(fn, path):
        if truncated:
            return
        add(f'\nFUNCTION {path} (parameters={fn.params}, children={len(fn.children)})')
        add('Constants:')
        for index, value in enumerate(fn.constants, 1):
            if truncated:
                return
            # repr preserves binary bytes rather than guessing an encoding.
            add(f'  K{index} = {value!r}')
        add('Instructions:')
        for pc, inst in enumerate(fn.instructions, 1):
            if truncated:
                return
            if inst is None:
                add(f'  {pc:05d}: <empty slot>')
                continue
            opcode, a, b, c, flags = inst
            def operand(value, bit):
                if value is None:
                    return '-'
                return f'K{value}' if flags & (1 << bit) else str(value)
            label = _OP_LABELS.get(opcode, 'UNKNOWN')
            add(f'  {pc:05d}: OP_{opcode} {label} A={operand(a, 0)} B={operand(b, 1)} C={operand(c, 2)}')
        for index, child in enumerate(fn.children):
            visit(child, f'{path}.{index}')

    visit(root, '0')
    if truncated:
        lines.append('\n[Report truncated at the output size limit.]')
    return VMReport('\n'.join(lines) + '\n', reader.functions, reader.instructions, reader.constants, truncated, len(recovered))


def inspect_vm(source: str) -> VMReport | None:
    """Return a partial report for the exact known interpreter, or None."""
    start = source.rfind(_MARKER)
    if start < 0 or len(source.encode('utf-8')) > MAX_BYTES:
        return None
    if hashlib.sha256(source[:start].encode('utf-8')).hexdigest() != _INTERPRETER_HASH:
        return None
    payload_start = start + len(_MARKER)
    end = source.find('"', payload_start)
    if end < 0 or source[end:] != _SUFFIX:
        return None
    try:
        data = unpack_payload(source[payload_start:end])
        reader = Reader(data)
        root = reader.function()
        if reader.pos != len(data):
            raise InvalidVM('Trailing bytes')
        return render(root, reader)
    except (InvalidVM, RecursionError):
        return None
