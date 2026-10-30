"""Small conservative Lua AST printer used for static specialization."""
from vendor.unprom import ast_nodes as A
from lua_cfg import indent
from lua_values import literal


def expr(n):
    if isinstance(n,A.Name): return n.name
    if isinstance(n,A.Number): return n.raw or repr(n.value)
    if isinstance(n,A.String): return n.raw if n.raw is not None else literal(n.value.encode('latin1'))
    if isinstance(n,A.Nil): return 'nil'
    if isinstance(n,A.TrueExpr): return 'true'
    if isinstance(n,A.FalseExpr): return 'false'
    if isinstance(n,A.Vararg): return '...'
    if isinstance(n,A.Paren): return '(' + expr(n.inner) + ')'
    if isinstance(n,A.BinOp): return '(' + expr(n.left) + ' ' + n.op + ' ' + expr(n.right) + ')'
    if isinstance(n,A.UnOp): return '(' + n.op + ' ' + expr(n.operand) + ')'
    if isinstance(n,A.Index):
        return expr(n.obj) + ('.' + n.key.value if n.dot and isinstance(n.key,A.String) else '[' + expr(n.key) + ']')
    if isinstance(n,A.Call): return expr(n.func) + '(' + ', '.join(expr(x) for x in n.args) + ')'
    if isinstance(n,A.MethodCall): return expr(n.obj) + ':' + n.method + '(' + ', '.join(expr(x) for x in n.args) + ')'
    if isinstance(n,A.Function):
        params=[expr(x) for x in n.params] + (['...'] if n.is_vararg else [])
        return 'function(' + ', '.join(params) + ')\n' + indent(block(n.body)) + '\nend'
    if isinstance(n,A.Table):
        fields=[]
        for kind,key,value in n.fields:
            prefix = '' if kind=='pos' else (key + ' = ' if kind=='name' else '[' + expr(key) + '] = ')
            fields.append(prefix + expr(value))
        return '{' + ', '.join(fields) + '}'
    raise ValueError('Unsupported expression: ' + type(n).__name__)


def statement(n):
    if isinstance(n,A.LocalAssign):
        return 'local ' + ', '.join(expr(x) for x in n.targets) + (' = ' + ', '.join(expr(x) for x in n.values) if n.values else '')
    if isinstance(n,A.Assign): return ', '.join(expr(x) for x in n.targets) + ' = ' + ', '.join(expr(x) for x in n.values)
    if isinstance(n,A.CallStat): return expr(n.call)
    if isinstance(n,A.Do): return 'do\n' + indent(block(n.body)) + '\nend'
    if isinstance(n,A.Return): return 'return' + (' ' + ', '.join(expr(x) for x in n.values) if n.values else '')
    if isinstance(n,A.Break): return 'break'
    if isinstance(n,A.Continue): return 'continue'
    if isinstance(n,A.If):
        pieces=[]
        for i,(condition,body) in enumerate(n.clauses):
            pieces.append(('if ' if i==0 else 'elseif ') + expr(condition) + ' then\n' + indent(block(body)))
        if n.orelse is not None: pieces.append('else\n' + indent(block(n.orelse)))
        return '\n'.join(pieces) + '\nend'
    if isinstance(n,A.While): return 'while ' + expr(n.cond) + ' do\n' + indent(block(n.body)) + '\nend'
    if isinstance(n,A.Repeat): return 'repeat\n' + indent(block(n.body)) + '\nuntil ' + expr(n.cond)
    if isinstance(n,A.NumericFor):
        bounds=[expr(n.start),expr(n.stop)] + ([expr(n.step)] if n.step is not None else [])
        return 'for ' + expr(n.var) + ' = ' + ', '.join(bounds) + ' do\n' + indent(block(n.body)) + '\nend'
    if isinstance(n,A.GenericFor):
        return 'for ' + ', '.join(expr(x) for x in n.names) + ' in ' + ', '.join(expr(x) for x in n.exprs) + ' do\n' + indent(block(n.body)) + '\nend'
    if isinstance(n,A.LocalFunction): return 'local ' + expr(n.name) + ' = ' + expr(n.func)
    if isinstance(n,A.FunctionDecl): return expr(n.target) + ' = ' + expr(n.func)
    raise ValueError('Unsupported statement: ' + type(n).__name__)


def block(n):
    return '\n'.join(statement(s) for s in n.stmts)
