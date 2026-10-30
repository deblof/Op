"""Specialize the fingerprinted Gist VM to explicit Lua, without running it."""
import math
from vendor.unprom import ast_nodes as A
from vendor.unprom.parser import Parser
from vendor.unprom.lexer import tokenize
from vendor.unprom.astutil import unparen
from vm_inspector import Reader, unpack_payload, _MARKER, _SUFFIX, recover_strings, matches_interpreter
from lua_values import literal
from lua_ast_source import block, expr
from lua_cfg import Step, render, indent


def _number(n):
    return A.Number(value=n,raw=str(n))


def _value(v):
    if isinstance(v,bytes): return A.String(raw=literal(v))
    if v is None: return A.Nil()
    if isinstance(v,bool): return A.TrueExpr() if v else A.FalseExpr()
    return A.Number(value=v,raw=literal(v))


def _test(node,opcode):
    node=unparen(node)
    if not (isinstance(node,A.BinOp) and isinstance(unparen(node.left),A.Name)
            and unparen(node.left).name=='v86' and isinstance(unparen(node.right),A.Number)):
        return None
    right=unparen(node.right).value
    return {'<':opcode<right,'<=':opcode<=right,'>':opcode>right,'>=':opcode>=right,
            '==':opcode==right,'~=':opcode!=right}.get(node.op)


def _choose(body,opcode):
    if len(body.stmts)!=1 or not isinstance(body.stmts[0],A.If): return body
    node=body.stmts[0]
    for i,(condition,child) in enumerate(node.clauses):
        result=_test(condition,opcode)
        if result is None:
            return A.Block(stmts=[A.If(clauses=node.clauses[i:],orelse=node.orelse)])
        if result: return _choose(child,opcode)
    return _choose(node.orelse,opcode) if node.orelse is not None else A.Block()


_NAMES={'v83':'values','v58':'upvalues','v59':'environment','v77':'state','v78':'top',
        'v79':'varargs','v84':'vararg_count','v82':'open_captures','v13':'unpack_values',
        'v76':'pack_values','v7':'table_insert','v10':'setmetatable','v12':'select'}


class Specialize(A.NodeTransformer):
    def __init__(self,fn,inst,pc):
        self.fn,self.inst,self.pc=fn,inst,pc
        self.targets=set()
        self.locals={}

    def visit_Index(self,n):
        if isinstance(n.obj,A.Name) and n.obj.name=='v85':
            key=unparen(n.key)
            if not isinstance(key,A.Number) or key.value not in (2,3,4):
                raise ValueError('Unsupported instruction operand')
            index=int(key.value)-2
            v=self.inst[index+1]
            if self.inst[4] & (1<<index): v=self.fn.constants[v-1]
            return _value(v)
        return self.generic_visit(n)

    def visit_Name(self,n):
        if n.name=='v77': return _number(self.pc)
        if n.name in _NAMES: return A.Name(name=_NAMES[n.name])
        if n.name.startswith('v') and n.name[1:].isdigit():
            if n.name not in self.locals: self.locals[n.name]='temporary_' + str(len(self.locals)+1)
            return A.Name(name=self.locals[n.name])
        return n

    def visit_Assign(self,n):
        if len(n.targets)==1 and isinstance(n.targets[0],A.Name) and n.targets[0].name=='v77':
            rhs=self.visit(n.values[0])
            def constant(x):
                x=unparen(x)
                if isinstance(x,A.Number): return x.value
                if isinstance(x,A.BinOp) and x.op in ('+','-'):
                    a,b=constant(x.left),constant(x.right)
                    return a+b if x.op=='+' else a-b
                raise ValueError('Computed jump is not statically known')
            dest=constant(rhs)+1
            if not math.isfinite(dest) or int(dest)!=dest: raise ValueError('Invalid jump')
            dest=int(dest)
            self.targets.add(dest)
            return A.Assign(targets=[A.Name(name='state')],values=[_number(dest)])
        return self.generic_visit(n)


def _state_assignment(body):
    if len(body.stmts)!=1: return None
    n=body.stmts[0]
    if (isinstance(n,A.Assign) and len(n.targets)==1 and isinstance(n.targets[0],A.Name)
            and n.targets[0].name=='state' and isinstance(n.values[0],A.Number)):
        return int(n.values[0].value)
    return None


def _returns(body):
    if not body.stmts: return False
    last=body.stmts[-1]
    return isinstance(last,A.Return) or (isinstance(last,A.Do) and _returns(last.body))


def translate(root,header):
    tree=Parser(tokenize(header)).parse_chunk()
    dispatch=None
    for node in A.walk(tree):
        if isinstance(node,A.If) and _test(node.clauses[0][0],71) is True:
            cond=unparen(node.clauses[0][0])
            if cond.op=='<=' and unparen(cond.right).value==71:
                dispatch=node
                break
    if dispatch is None: raise ValueError('Known interpreter dispatcher was not found')
    handlers={op:_choose(A.Block(stmts=[dispatch]),op) for op in range(144)}
    functions=[]
    def collect(fn,path):
        index=len(functions)+1
        functions.append((fn,path))
        for i,c in enumerate(fn.children): collect(c,path+'.'+str(i))
        return index
    collect(root,'0')
    indices={id(fn):i for i,(fn,_) in enumerate(functions,1)}
    annotations={(path,pc+3):data for path,pc,data in recover_strings(root)}
    output=['-- Statically translated Lua. Original variable names are unavailable.',
            '-- Complex control flow may retain explicit state blocks.',
            'local environment = (getfenv and getfenv()) or _ENV',
            'local unpack_values = unpack or table.unpack',
            'local table_insert = table.insert',
            'local function pack_values(...) return {...}, select("#", ...) end',
            'local functions = {}']
    structured_count=translated_count=0
    for index,(fn,path) in enumerate(functions,1):
        steps=[];pc=1;exit_address=len(fn.instructions)+1
        while pc<exit_address:
            inst=fn.instructions[pc-1]
            if inst is None: raise ValueError('Empty instruction outside capture data')
            opcode,a,b,c,flags=inst
            following=pc+1
            condition=None;dynamic=False;targets=(following,)
            if opcode==136:
                if flags or not isinstance(c,int) or not 0<=b<len(fn.children) or not 0<=c<=256:
                    raise ValueError('Unsupported closure operands')
                references=[]
                for i in range(c):
                    if pc+i>=len(fn.instructions): raise ValueError('Truncated captures')
                    capture=fn.instructions[pc+i]
                    if capture is None or capture[4]: raise ValueError('Invalid capture')
                    table='values' if capture[0]==128 else 'upvalues'
                    references.append(f'[{i}] = {{{table}, {capture[2]}}}')
                child=indices[id(fn.children[b])]
                code=('local references = {'+', '.join(references)+'}\n'
                      'local captured = setmetatable({}, {\n'
                      '    __index = function(_, key)\n        local cell = references[key]\n        return cell[1][cell[2]]\n    end,\n'
                      '    __newindex = function(_, key, value)\n        local cell = references[key]\n        cell[1][cell[2]] = value\n    end\n})\n'
                      f'for i = 1, {c} do open_captures[#open_captures + 1] = references end\n'
                      f'values[{a}] = function(...) return functions[{child}](captured, environment, ...) end')
                following=pc+c+1
                targets=(following,)
            else:
                if opcode not in handlers: raise ValueError('Unsupported opcode')
                special=Specialize(fn,inst,pc)
                body=special.visit(A.clone(handlers[opcode]))
                declarations = set()
                for node in A.walk(body):
                    if isinstance(node, A.LocalAssign): declarations.update(n.name for n in node.targets)
                    if isinstance(node, A.NumericFor): declarations.add(node.var.name)
                    if isinstance(node, A.GenericFor): declarations.update(n.name for n in node.names)
                    if isinstance(node, A.Function): declarations.update(n.name for n in node.params)
                temporaries = {node.name for node in A.walk(body)
                               if isinstance(node, A.Name) and node.name.startswith('temporary_')}
                if temporaries - declarations:
                    raise ValueError('Unresolved interpreter local')
                unconditional=_state_assignment(body)
                if unconditional is not None:
                    code='';targets=(unconditional,)
                elif (len(body.stmts)==1 and isinstance(body.stmts[0],A.If)
                      and len(body.stmts[0].clauses)==1 and body.stmts[0].orelse is not None
                      and _state_assignment(body.stmts[0].clauses[0][1]) is not None
                      and _state_assignment(body.stmts[0].orelse) is not None):
                    node=body.stmts[0]
                    condition=expr(node.clauses[0][0]);code=''
                    targets=(_state_assignment(node.clauses[0][1]),_state_assignment(node.orelse))
                else:
                    code=block(body)
                    if _returns(body): targets=(exit_address,)
                    elif special.targets:
                        code=f'state = {following}\n'+code
                        targets=tuple(sorted(special.targets|{following}));dynamic=True
                if any(isinstance(n,A.Name) and n.name in ('temporary_0','v85','v73','v74','v29') for n in A.walk(body)):
                    raise ValueError('Untranslated VM reference')
            if 'local ' in code or 'return' in code:
                code='do\n'+indent(code)+'\nend'
            candidate=annotations.get((path,pc))
            if candidate is not None: code='-- Candidate decoded string: '+literal(candidate)+'\n'+code
            steps.append(Step(pc,code,targets,condition,dynamic))
            pc=following
            translated_count+=1
        body,structured=render(steps,exit_address)
        structured_count+=structured
        setup=('local values, varargs, open_captures = {}, {}, {}\nlocal arguments = {...}\n'
               'local argument_count = select("#", ...)\nlocal top = -1\n'
               f'for i = 0, argument_count - 1 do\n    if i >= {fn.params} then\n'
               f'        varargs[i - {fn.params}] = arguments[i + 1]\n    else\n        values[i] = arguments[i + 1]\n    end\nend\n'
               f'local vararg_count = argument_count - {fn.params}\n')
        piece=f'\n-- Function {index} (prototype {path})\nfunctions[{index}] = function(upvalues, environment, ...)\n'+indent(setup+body)+'\nend'
        output.append(piece)
    output.append('\nreturn functions[1]({}, environment, ...)\n')
    result='\n'.join(output)
    if len(result.encode())>8_000_000: raise ValueError('Translated source exceeds output limit')
    return result,structured_count,translated_count


def lift(source):
    start=source.rfind(_MARKER)
    if start<0 or not matches_interpreter(source[:start]): return None
    end=source.find('"',start+len(_MARKER))
    if end<0 or source[end:]!=_SUFFIX: return None
    reader=Reader(unpack_payload(source[start+len(_MARKER):end]))
    root=reader.function()
    if reader.pos!=len(reader.data): raise ValueError('Trailing VM data')
    return translate(root,source[:start])
