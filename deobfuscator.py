"""Decode a small, explicitly supported subset of Lua without executing it."""

import base64
import binascii
from dataclasses import dataclass
import re

MAX_BYTES = 1_000_000
MAX_INPUT_BYTES = 2_000_000
MAX_LAYERS = 8
MAX_NESTING = 64
NUMBER = re.compile(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)')
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
MAX_LOCALS = 128
MAX_TOTAL_LOCAL_BYTES = 2 * MAX_BYTES
RESERVED = set('and break do else elseif end false for function goto if in local nil not or repeat return then true until while'.split())
PROTECTED = {'string', 'table', 'base64', 'base64_decode', 'base64decode', 'hex', 'hex_decode', 'hexdecode', 'bit', 'bit32', 'crypt', 'syn', 'load', 'loadstring'}


class Unsupported(ValueError):
    """The input is not a supported literal expression."""


@dataclass(frozen=True)
class Result:
    source: str
    layers: int


class Parser:
    def __init__(self, source: str):
        self.source = source
        self.pos = 0
        self.locals = {}

    def space(self):
        while self.pos < len(self.source) and self.source[self.pos] in ' \t\r\n\f\v':
            self.pos += 1

    def take(self, value: str) -> bool:
        self.space()
        if self.source.startswith(value, self.pos):
            self.pos += len(value)
            return True
        return False

    def require(self, value: str):
        if not self.take(value):
            raise Unsupported()

    def keyword(self, value: str) -> bool:
        self.space()
        end = self.pos + len(value)
        if (self.source.startswith(value, self.pos)
                and (end == len(self.source) or not (self.source[end].isascii() and (self.source[end].isalnum() or self.source[end] == '_')))):
            self.pos = end
            return True
        return False

    def expression(self, depth: int = 0) -> bytes:
        if depth > MAX_NESTING:
            raise Unsupported()
        pieces = [self.term(depth)]
        size = len(pieces[0])
        while self.take('..'):
            piece = self.term(depth)
            size += len(piece)
            if size > MAX_BYTES:
                raise Unsupported()
            pieces.append(piece)
        return b''.join(pieces)

    def integer(self) -> int:
        self.space()
        match = NUMBER.match(self.source, self.pos)
        if not match or len(match.group()) > 10:
            raise Unsupported()
        raw = match.group()
        self.pos = match.end()
        return int(raw, 16 if raw.lower().startswith('0x') else 10)

    def byte_value(self, depth: int) -> int:
        if depth > MAX_NESTING:
            raise Unsupported()
        if self.keyword('bit32.bxor') or self.keyword('bit.bxor'):
            self.require('(')
            value = self.byte_value(depth + 1)
            while self.take(','):
                value ^= self.byte_value(depth + 1)
            self.require(')')
            return value
        number = self.integer()
        if number > 255:
            raise Unsupported()
        return number

    def term(self, depth: int) -> bytes:
        if depth > MAX_NESTING:
            raise Unsupported()
        self.space()
        if self.take('('):
            value = self.expression(depth + 1)
            self.require(')')
            return value
        if self.keyword('string.char'):
            self.require('(')
            values = bytearray()
            if not self.take(')'):
                while True:
                    number = self.byte_value(depth + 1)
                    if len(values) >= MAX_BYTES:
                        raise Unsupported()
                    values.append(number)
                    if self.take(')'):
                        break
                    self.require(',')
            return bytes(values)
        if self.keyword('string.reverse'):
            self.require('(')
            value = self.expression(depth + 1)
            self.require(')')
            return value[::-1]
        if self.keyword('string.rep'):
            self.require('(')
            value = self.expression(depth + 1)
            self.require(',')
            count = self.integer()
            separator = b''
            if self.take(','):
                separator = self.expression(depth + 1)
            self.require(')')
            size = len(value) * count + len(separator) * max(0, count - 1)
            if size > MAX_BYTES or count > MAX_BYTES:
                raise Unsupported()
            return separator.join([value] * count)
        if self.keyword('table.concat'):
            self.require('(')
            self.require('{')
            values = []
            size = 0
            if not self.take('}'):
                while True:
                    value = self.expression(depth + 1)
                    size += len(value)
                    if size > MAX_BYTES or len(values) >= MAX_BYTES:
                        raise Unsupported()
                    values.append(value)
                    if self.take('}'):
                        break
                    self.require(',')
                    if self.take('}'):
                        break
            separator = b''
            if self.take(','):
                separator = self.expression(depth + 1)
            self.require(')')
            if size + len(separator) * max(0, len(values) - 1) > MAX_BYTES:
                raise Unsupported()
            return separator.join(values)
        for name in ('syn.crypt.base64.decode', 'crypt.base64decode', 'base64.decode',
                     'base64_decode', 'base64decode'):
            if self.keyword(name):
                self.require('(')
                value = self.expression(depth + 1)
                self.require(')')
                try:
                    return base64.b64decode(value, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise Unsupported() from exc
        for name in ('crypt.hexdecode', 'hex.decode', 'hex_decode', 'hexdecode'):
            if self.keyword(name):
                self.require('(')
                value = self.expression(depth + 1)
                self.require(')')
                if len(value) % 2 or not re.fullmatch(rb'[0-9a-fA-F]*', value):
                    raise Unsupported()
                return bytes.fromhex(value.decode('ascii'))
        match = IDENTIFIER.match(self.source, self.pos)
        if match:
            name = match.group()
            if name not in self.locals:
                raise Unsupported()
            self.pos = match.end()
            return self.locals[name]
        return self.string()

    def string(self) -> bytes:
        self.space()
        match = re.match(r'\[(=*)\[', self.source[self.pos:])
        if match:
            start = self.pos + len(match.group())
            end = self.source.find(']' + match.group(1) + ']', start)
            if end < 0:
                raise Unsupported()
            text = self.source[start:end]
            # Lua ignores one initial newline in long strings.
            if text.startswith('\r\n') or text.startswith('\n\r'):
                text = text[2:]
            elif text.startswith(('\n', '\r')):
                text = text[1:]
            self.pos = end + len(match.group(1)) + 2
            # Lua normalizes all newline conventions in long strings.
            text = re.sub(r'\r\n|\n\r|\r', '\n', text)
            return text.encode('utf-8')
        if self.pos >= len(self.source) or self.source[self.pos] not in "\"'":
            raise Unsupported()
        quote = self.source[self.pos]
        self.pos += 1
        value = bytearray()
        escapes = {'a': 7, 'b': 8, 'f': 12, 'n': 10, 'r': 13, 't': 9, 'v': 11,
                   '\\': 92, '"': 34, "'": 39}
        while self.pos < len(self.source):
            char = self.source[self.pos]
            self.pos += 1
            if char == quote:
                return bytes(value)
            if char in '\r\n':
                raise Unsupported()
            if char != '\\':
                value.extend(char.encode('utf-8'))
                continue
            if self.pos >= len(self.source):
                raise Unsupported()
            char = self.source[self.pos]
            self.pos += 1
            if char in escapes:
                value.append(escapes[char])
            elif char in '\r\n':
                if self.pos < len(self.source) and self.source[self.pos] in '\r\n' and self.source[self.pos] != char:
                    self.pos += 1
                value.append(10)
            elif char == 'z':
                self.space()
            elif char == 'x':
                digits = self.source[self.pos:self.pos + 2]
                if not re.fullmatch(r'[0-9a-fA-F]{2}', digits):
                    raise Unsupported()
                value.append(int(digits, 16))
                self.pos += 2
            elif char in '0123456789':
                start = self.pos - 1
                while self.pos < len(self.source) and self.pos - start < 3 and self.source[self.pos] in '0123456789':
                    self.pos += 1
                number = int(self.source[start:self.pos])
                if number > 255:
                    raise Unsupported()
                value.append(number)
            else:
                raise Unsupported()
        raise Unsupported()


def unwrap(source: str) -> str:
    parser = Parser(source)
    total_local_bytes = 0
    while parser.keyword('local'):
        parser.space()
        match = IDENTIFIER.match(parser.source, parser.pos)
        if not match:
            raise Unsupported()
        name = match.group()
        if name in RESERVED or name in PROTECTED or name in parser.locals or len(parser.locals) >= MAX_LOCALS:
            raise Unsupported()
        parser.pos = match.end()
        parser.require('=')
        value = parser.expression()
        total_local_bytes += len(value)
        if total_local_bytes > MAX_TOTAL_LOCAL_BYTES:
            raise Unsupported()
        parser.locals[name] = value
        parser.take(';')
    parser.keyword('return')
    if not (parser.keyword('loadstring') or parser.keyword('load')):
        raise Unsupported()
    parser.require('(')
    decoded = parser.expression()
    parser.require(')')
    # Accept an immediately invoked compiled chunk, with optional semicolon.
    if parser.take('('):
        parser.require(')')
    parser.take(';')
    parser.space()
    if parser.pos != len(source) or len(decoded) > MAX_BYTES:
        raise Unsupported()
    try:
        return decoded.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise Unsupported() from exc


def deobfuscate(source: str) -> Result:
    if len(source.encode('utf-8')) > MAX_INPUT_BYTES:
        raise ValueError('The input exceeds the 2 MB limit.')
    layers = 0
    while layers < MAX_LAYERS:
        try:
            decoded = unwrap(source)
        except Unsupported:
            break
        if decoded == source:
            break
        source = decoded
        layers += 1
    return Result(source, layers)


def remote_url(source: str) -> str | None:
    """Recognize a direct URL or a whole-script Roblox HttpGet wrapper.

    URL authorization and downloading are handled by the caller. No Lua is run.
    """
    source = source.strip()
    if source.startswith(('https://', 'http://')) and '\n' not in source and '\r' not in source:
        return source
    parser = Parser(source)
    try:
        parser.keyword('return')
        if not (parser.keyword('loadstring') or parser.keyword('load')):
            return None
        parser.require('(')
        if not (parser.keyword('game:HttpGet') or parser.keyword('game:HttpGetAsync')):
            return None
        parser.require('(')
        address = parser.expression().decode('utf-8')
        if parser.take(',') and not (parser.keyword('true') or parser.keyword('false')):
            return None
        parser.require(')')
        parser.require(')')
        if parser.take('('):
            parser.require(')')
        parser.take(';')
        parser.space()
        return address if parser.pos == len(source) else None
    except (Unsupported, UnicodeDecodeError):
        return None
