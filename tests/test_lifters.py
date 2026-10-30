"""Behavior checks use only small, independently authored Lua programs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from aryq_lifter import translate as translate_aryq
from gist_lifter import translate as translate_gist
from vm_inspector import Function

RUNTIME = os.environ.get('LUAU_BIN') or shutil.which('luau') or shutil.which('lua5.1')


def assemble(rows):
    labels = {}
    pc = 1
    for row in rows:
        if isinstance(row, str): labels[row] = pc
        else: pc += 1 + len(row[1])
    stream = []
    for row in rows:
        if isinstance(row, str): continue
        opcode, args = row
        stream.append(opcode + ((len(stream) + 1) * 172 + 192) % 256)
        for value in args:
            if isinstance(value, str): value = labels[value]
            stream.append(value + ((len(stream) + 1) * 121 + 125) % 256)
    return stream


def execute(source):
    wrapper = ('local function recovered(...)\n' + source + '\nend\n'
               'local function show(...)\n print(select("#", ...))\n'
               ' for i = 1, select("#", ...) do print(i, type((select(i, ...))), tostring((select(i, ...)))) end\nend\n'
               'show(recovered())\n')
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'authored.lua'
        path.write_text(wrapper)
        result = subprocess.run([RUNTIME, str(path)], capture_output=True, timeout=5)
    if result.returncode:
        raise AssertionError(result.stderr.decode()[:2000])
    return result.stdout


@unittest.skipUnless(RUNTIME, 'A Lua interpreter is required only for authored behavior tests')
class AryqTranslationTests(unittest.TestCase):
    def check_program(self, rows, pool, original, *, captures='{}', children=()):
        streams = [assemble(rows)] + [assemble(code) for code, _ in children]
        pools = [pool] + [constants for _, constants in children]
        source, _, _ = translate_aryq(streams, pools, captures)
        self.assertNotIn('DogmQs0UBq', source)
        self.assertEqual(execute(source), execute(original))
        return source

    def test_arithmetic_and_comparisons(self):
        operations = [(34,'-'),(41,'+'),(54,'*'),(102,'^'),(136,'/'),(174,'%'),
                      (25,'<='),(61,'<'),(67,'>'),(86,'>='),(169,'=='),(225,'~=')]
        rows = [(248,[1,1]),(248,[2,2])]
        for i,(opcode,_) in enumerate(operations,3): rows.append((opcode,[i,1,2]))
        rows.append((42,[3,len(operations)]))
        expected = 'return ' + ', '.join('7.5 '+op+' 2' for _,op in operations)
        self.check_program(rows,[('number',b'7.5'),('number',b'2')],expected)

    def test_branches_and_loop(self):
        rows=[(248,[1,1]),(248,[2,2]),(248,[3,3]),(248,[4,2]),'loop',
              (25,[5,2,3]),(124,[5,'done']),(41,[1,1,2]),(41,[2,2,4]),(239,['loop']),
              'done',(42,[1,1])]
        output=self.check_program(rows,[('number',b'0'),('number',b'1'),('number',b'5')],
                                  'local total=0 for i=1,5 do total=total+i end return total')
        self.assertNotIn('local state',output)

    def test_closure_and_shared_upvalue_mutation(self):
        root=[(248,[1,1]),(154,[1]),(80,[2,2,1,0,1]),(158,[2,0]),(158,[2,0]),(246,[3,1]),(42,[3,1])]
        child=[(236,[1,1]),(248,[2,1]),(41,[1,1,2]),(14,[1,1]),(42,[1,1])]
        self.check_program(root,[('number',b'5')],
                           'local n=5 local function add() n=n+3 return n end add() add() return n',
                           children=((child,[('number',b'3')]),))

    def test_missing_fixed_arguments_produce_empty_varargs(self):
        self.check_program([(161,[1,3]),(63,[1])],[], 'return')

    def test_multivalue_call_and_trailing_nil(self):
        rows=[(207,[1,1]),(248,[2,2]),(11,[3,1,2]),(248,[4,3]),(248,[5,4]),
              (187,[3,2]),(122,[3,6,3]),(42,[6,3])]
        self.check_program(rows,[('string',b'string'),('string',b'find'),('string',b'hello'),('string',b'l')],
                           'local a,b=string.find("hello","l") return a,b,nil')

    def test_variable_arguments_preserve_nil_and_multiple_results(self):
        rows=[(193,[1,1]),(248,[2,1]),(193,[3,2]),(37,[1,1]),(63,[1])]
        self.check_program(rows,[('string',b'x')], 'return 3,"x",nil,"z"',
                           captures='{function(...) return select("#",...),... end, {n=2,[2]="z"}}')

    def test_variable_return(self):
        rows=[(248,[1,1]),(193,[2,1]),(4,[1,1])]
        self.check_program(rows,[('number',b'9')],'return 9,nil,"z"',captures='{{n=2,[2]="z"}}')

    def test_tables_cells_and_unary_operations(self):
        rows=[(72,[1]),(248,[2,1]),(248,[3,2]),(141,[1,2,3]),(11,[4,1,2]),
              (214,[5,4]),(248,[6,3]),(1,[5,6]),(246,[7,5]),(202,[8,7]),(228,[9,2]),
              (56,[10]),(204,[11,10]),(42,[7,5])]
        self.check_program(rows,[('string',b'key'),('number',b'40'),('number',b'2')],
                           'return 2,-2,3,true,false')

    def test_call_variants_keep_argument_counts_and_result_counts(self):
        for opcode in (37,45,135,150,50,182):
            with self.subTest(opcode=opcode):
                variable = opcode in (37,45,135,150)
                rows=[(193,[1,1]),(248,[2,1]),(193,[3,2])]
                args=[1,1,3] if opcode in (150,50) else [1,1]
                rows.append((opcode,args))
                if opcode==45:
                    rows.append((75,[]));expected='print(3,"x",nil,"z") return'
                    callback='function(...) print(select("#",...),...) end'
                elif opcode==37:
                    rows.append((63,[1]));expected='return "x",nil,"z"'
                    callback='function(...) return ... end'
                elif opcode in (150,50):
                    rows.append((42,[1,3]));expected='return "x",nil,' + ('"z"' if variable else 'nil')
                    callback='function(...) return ... end'
                else:
                    rows.append((42,[1,1]));expected='return "x"'
                    callback='function(...) return ... end'
                self.check_program(rows,[('string',b'x')],expected,
                                   captures='{'+callback+', {n=2,[2]="z"}}')

    def test_global_write_move_concat_boolean_and_nil(self):
        rows=[(248,[1,2]),(6,[1,1]),(207,[2,1]),(109,[3,2]),(248,[4,3]),
              (118,[5,3,4]),(137,[6]),(159,[7]),(42,[5,3])]
        self.check_program(rows,[('string',b'authored_fixture'),('string',b'hello'),('string',b'!')],
                           'authored_fixture="hello" return authored_fixture.."!",false,nil')

    def test_list_assignment_and_truthy_branch(self):
        rows=[(72,[1]),(193,[2,1]),(237,[1,2,0]),(248,[3,1]),(11,[4,1,3]),
              (24,[4,'done']),(159,[4]),'done',(42,[4,1])]
        self.check_program(rows,[('number',b'1')],'return "yes"',captures='{{n=3,"yes",nil,"last"}}')

    @unittest.skipIf(RUNTIME and 'lua5.1' in RUNTIME, 'Floor division requires Luau')
    def test_floor_division(self):
        self.check_program([(248,[1,1]),(248,[2,2]),(244,[3,1,2]),(42,[3,1])],
                           [('number',b'-7'),('number',b'2')],'return -7//2')

    def test_invalid_targets_and_truncated_captures_are_declined(self):
        with self.assertRaises(ValueError): translate_aryq([assemble([(239,[2])])],[[]])
        with self.assertRaises(ValueError): translate_aryq([assemble([(80,[1,1,2,0,1])])],[[]])


# A small independently authored dispatch grammar for specialization checks.
# It is never executed; the tests execute only its translated instructions.
HEADER = '''
if v86<=71 then
  if v86==1 then
    local saved={}
    for k=1,#v82 do
      local refs=v82[k]
      for i=0,#refs do
        local cell=refs[i]
        if cell[1]==v83 and cell[2]>=v85[2] then saved[cell[2]]=cell[1][cell[2]] cell[1]=saved end
      end
    end
  elseif v86==9 then v83[v85[2]]=v85[3]
  elseif v86==24 then v83[v85[2]]=v83[v85[3]]+v83[v85[4]]
  elseif v86==26 then do return v83[v85[2]] end
  elseif v86==43 then v58[v85[3]]=v83[v85[2]]
  elseif v86==47 then if not v83[v85[2]] then v77=v77+1 else v77=v85[3] end
  elseif v86==57 then v83[v85[2]]=v59[v85[3]]
  elseif v86==59 then do return end
  elseif v86==68 then v77=v85[3]
  else v83[v85[2]]=v83[v85[3]] end
else
  if v86==83 then v83[v85[2]]=v58[v85[3]]
  elseif v86==86 then v83[v85[2]]=v83[v85[3]]
  elseif v86==143 then local a=v85[2] local t,n=v76(v83[a](v13(v83,a+1,v85[3])))
    v78=n+a-1 for j=a,v78 do v83[j]=t[j-a+1] end
  else v83[v85[2]]=v83[v85[3]] end
end
'''


@unittest.skipUnless(RUNTIME, 'A Lua interpreter is required only for authored behavior tests')
class GistTranslationTests(unittest.TestCase):
    def check_program(self,fn,original):
        source,_,_=translate_gist(fn,HEADER)
        self.assertNotIn('v85',source)
        self.assertEqual(execute(source),execute(original))
        return source

    def test_values_constants_and_arithmetic(self):
        fn=Function(0,[7.0,9.0],[(9,0,1,None,2),(9,1,2,None,2),(24,2,0,1,0),(26,2,0,0,0)],[])
        self.check_program(fn,'return 7+9')

    def test_branch_jump_offset(self):
        fn=Function(0,[False,b'wrong',b'correct'],[(9,0,1,None,2),(47,0,4,0,0),
                    (68,0,5,None,0),(9,1,2,None,2),(9,1,3,None,2),(26,1,0,0,0)],[])
        self.check_program(fn,'return "correct"')

    def test_shared_reference_and_upvalue_write(self):
        child=Function(0,[3.0],[(83,0,0,0,0),(9,1,1,None,2),(24,0,0,1,0),(43,0,0,0,0),(26,0,0,0,0)],[])
        root=Function(0,[5.0],[(9,0,1,None,2),(136,1,0,1,0),(128,0,0,0,0),
                              (143,1,1,0,0),(26,0,0,0,0)],[child])
        self.check_program(root,'local n=5 local function add() n=n+3 return n end add() return n')

    def test_closing_upvalues_preserves_cells_after_register_reuse(self):
        child=Function(0,[3.0],[(83,0,0,0,0),(9,1,1,None,2),(24,0,0,1,0),(43,0,0,0,0),(26,0,0,0,0)],[])
        root=Function(0,[5.0,99.0],[(9,0,1,None,2),(136,1,0,1,0),(128,0,0,0,0),
                                   (1,0,0,0,0),(9,0,2,None,2),(143,1,1,0,0),(26,1,0,0,0)],[child])
        self.check_program(root,'local f do local n=5 f=function() n=n+3 return n end end local other=99 return f()')
