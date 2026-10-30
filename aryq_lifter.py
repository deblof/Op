"""Translate the recognized Aryq instruction set to explicit Lua operations.

Only the fingerprint-gated inspector supplies real scripts to this module.
This is static translation: no Lua instructions are executed during recovery.
"""
import re
from collections import Counter
from lua_cfg import Step, render, indent
from lua_values import literal

_ARITY = {1:2,4:2,6:2,11:3,14:2,24:2,25:3,34:3,37:2,41:3,42:2,45:2,50:3,
          54:3,56:1,61:3,63:1,67:3,72:1,75:0,80:3,86:3,102:3,109:2,118:3,
          122:3,124:2,135:2,136:3,137:1,141:3,150:3,154:1,158:2,159:1,161:2,
          169:3,174:3,182:2,187:2,193:2,202:2,204:2,207:2,214:2,225:3,228:2,
          236:2,237:3,239:1,244:3,246:2,248:2}
_BINARY = {25:'<=',34:'-',41:'+',54:'*',61:'<',67:'>',86:'>=',102:'^',118:'..',
           136:'/',169:'==',174:'%',225:'~=',244:'//'}
_NUMERIC = re.compile(rb'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')


def decode(stream):
    position = 0
    instructions = []
    while position < len(stream):
        address = position + 1
        opcode = stream[position] - (address * 172 + 192) % 256
        position += 1
        if opcode not in _ARITY:
            raise ValueError(f'Unsupported Aryq instruction {opcode}')
        arguments = []
        count = _ARITY[opcode]
        while len(arguments) < count:
            if position == len(stream):
                raise ValueError('Truncated Aryq instruction')
            argument = stream[position] - ((position + 1) * 121 + 125) % 256
            position += 1
            arguments.append(argument)
            if opcode == 80 and len(arguments) == 3:
                if not 0 <= argument <= 1000:
                    raise ValueError('Invalid capture count')
                count += 2 * argument
        instructions.append((address, opcode, arguments, position + 1))
    return instructions


def _scope(code):
    if 'local ' in code or code.startswith('return'):
        return 'do\n' + indent(code) + '\nend'
    return code


def translate_function(stream, pool, function_count):
    steps = []
    exit_address = len(stream) + 1
    counts = Counter()
    def r(n):
        return f'values[{n}]'
    def k(n):
        if not 1 <= n <= len(pool):
            raise ValueError('Invalid Aryq constant reference')
        kind, value = pool[n - 1]
        if kind == 'number':
            if not _NUMERIC.fullmatch(value) or len(value) > 100:
                raise ValueError('Unsupported numeric constant')
            return literal(float(value))
        return literal(value)
    for address, opcode, args, following in decode(stream):
        counts[opcode] += 1
        a, b, c = (args + [0, 0, 0])[:3]
        targets, condition = (following,), None
        code = ''
        if opcode in _BINARY:
            code = f'{r(a)} = {r(b)} {_BINARY[opcode]} {r(c)}'
            if opcode == 41: code += '\nvalues[0] = nil'
            if opcode == 67: code += '\nvalues[0] = 0'
        elif opcode == 1: code = f'{r(a)}[1] = {r(b)}\nvalues[0] = true'
        elif opcode == 6: code = f'environment[{k(a)}] = {r(b)}'
        elif opcode == 11: code = f'{r(a)} = {r(b)}[{r(c)}]'
        elif opcode == 14: code = f'upvalues[{a}][1] = {r(b)}\nvalues[0] = nil'
        elif opcode in (24,124):
            condition = r(a) if opcode == 24 else 'branch_test'
            if opcode == 124: code = f'branch_test = not {r(a)}\nvalues[0] = false'
            targets = (b, following)
        elif opcode == 56: code = f'{r(a)} = true'
        elif opcode == 72: code = f'{r(a)} = {{}}'
        elif opcode == 109: code = f'{r(a)} = {r(b)}'
        elif opcode == 137: code = f'{r(a)} = false\nvalues[0] = true'
        elif opcode == 141: code = f'{r(a)}[{r(b)}] = {r(c)}'
        elif opcode == 154: code = f'{r(a)} = {{{r(a)}}}'
        elif opcode == 159: code = f'{r(a)} = nil\nvalues[0] = 0'
        elif opcode == 193: code = f'{r(a)} = upvalues[{b}]\nvalues[0] = nil'
        elif opcode == 202: code = f'{r(a)} = -{r(b)}\nvalues[0] = 0'
        elif opcode == 204: code = f'{r(a)} = not {r(b)}\nvalues[0] = values[0]'
        elif opcode == 207: code = f'{r(a)} = environment[{k(b)}]'
        elif opcode == 214: code = f'{r(a)} = {{{r(b)}}}\nvalues[0] = true'
        elif opcode == 228: code = f'{r(a)} = #{r(b)}'
        elif opcode == 236: code = f'{r(a)} = upvalues[{b}][1]'
        elif opcode == 239: code, targets = 'values[0] = false', (a,)
        elif opcode == 246: code = f'{r(a)} = {r(b)}[1]'
        elif opcode == 248: code = f'{r(a)} = {k(b)}'
        elif opcode == 80:
            if not 1 <= b <= function_count:
                raise ValueError('Invalid function reference')
            captures = []
            for i in range(c):
                kind, index = args[3 + 2*i:5 + 2*i]
                captures.append(r(index) if kind == 0 else f'upvalues[{index}]')
            code = ('local captures = {' + ', '.join(captures) + '}\n' +
                    f'{r(a)} = function(...)\n    return functions[{b}](captures, ...)\nend')
        elif opcode in (4,42,63,75):
            targets = (exit_address,)
            if opcode == 75: code = 'return'
            elif opcode == 63: code = f'local results = {r(a)}\nreturn unpack_values(results, 1, results.n)'
            elif opcode == 42: code = f'return unpack_values(values, {a}, {a+b-1})'
            else:
                code = (f'local tail = {r(a+b)}\nlocal results, result_count = append_arguments(values, {a}, {b}, tail)\n'
                        'return unpack_values(results, 1, result_count)')
        elif opcode == 122:
            code = f'local results = {r(a)}\nfor i = 1, {c} do values[{b} + i - 1] = results[i] end'
        elif opcode == 161:
            code = (f'local results = {{n = 0}}\nfor i = {b+1}, argc do\n'
                    f'    results.n = results.n + 1\n    results[results.n] = values[i]\nend\n{r(a)} = results')
        elif opcode == 237:
            code = f'local results = {r(b)}\nfor i = 1, results.n do {r(a)}[{c} + i] = results[i] end'
        elif opcode in (37,45,50,135,150,158,182,187):
            variable = opcode in (37,45,135,150)
            if variable:
                code = (f'local callee = {r(a)}\nlocal tail = {r(a+b+1)}\n'
                        f'local arguments, argument_count = append_arguments(values, {a+1}, {b}, tail)\n')
                call = 'callee(unpack_values(arguments, 1, argument_count))'
            else:
                call = f'{r(a)}(unpack_values(values, {a+1}, {a+b}))'
            if opcode in (37,187): code += f'{r(a)} = pack_values({call})'
            elif opcode in (135,182): code += f'{r(a)} = {call}'
            elif opcode in (45,158):
                code += call
                if opcode == 45: code += '\nvalues[0] = true'
            else:
                code += f'local results = {{{call}}}\nfor i = 1, {c} do values[{a} + i - 1] = results[i] end'
        else:
            raise ValueError('Unhandled Aryq instruction')
        steps.append(Step(address, _scope(code), targets, condition))
    body, structured = render(steps, exit_address)
    return ('local values = {...}\nlocal argc = select("#", ...)\nlocal branch_test\n' + body), structured, sum(counts.values())


def translate(streams, pools, captures='{}'):
    if len(streams) != len(pools) or len(streams) > 1000:
        raise ValueError('Mismatched Aryq functions and constants')
    output = ['-- Statically translated Lua. Original names are unavailable.',
              '-- Uses the standard-runtime key assumption. Keep the original.',
              '-- Complex control flow may retain explicit state blocks.',
              'local environment = (function()\n    local ok, env = pcall(getfenv)\n'
              '    if ok and type(env) == "table" then return env end\n    return _G or {}\nend)()',
              'local unpack_values = table.unpack or unpack',
              'local pack_values = table.pack or function(...) return {n = select("#", ...), ...} end',
              'local function append_arguments(values, start, count, tail)\n    local result = {}\n'
              '    for i = 1, count do result[i] = values[start + i - 1] end\n'
              '    local extra = tail.n\n    for i = 1, extra do result[count + i] = tail[i] end\n    return result, count + extra\nend',
              'local functions = {}']
    structured_count = instruction_count = 0
    size = 0
    for index, (stream, pool) in enumerate(zip(streams, pools), 1):
        body, structured, count = translate_function(stream, pool, len(streams))
        structured_count += structured
        instruction_count += count
        piece = f'\n-- Function {index}\nfunctions[{index}] = function(upvalues, ...)\n' + indent(body) + '\nend'
        output.append(piece)
        size += len(piece)
        if size > 8_000_000:
            raise ValueError('Translated Lua exceeds the output limit')
    output.append(f'\nreturn functions[1]({captures}, ...)\n')
    return '\n'.join(output), structured_count, instruction_count
