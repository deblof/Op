import unittest
import shutil
import subprocess
from unittest.mock import patch

from aryq_inspector import _constants, _decrypt, _inspect, _seed, _varint, inspect_aryq


class AryqInspectionTests(unittest.TestCase):
    def test_stream_decoder_matches_independent_lua_fixture(self):
        # Expected bytes produced by the separate self-authored Lua 5.1 reference.
        raw = bytes([0, 1, 2, 3, 255, 128, 97, 98, 99])
        self.assertEqual(_decrypt(raw, 12345, 17).hex(), 'f3dde0132949ece965')

    def test_varints_and_constants(self):
        self.assertEqual(_varint(b'\xac\x02', 0), (300, 2))
        with self.assertRaises(ValueError):
            _varint(b'\x80', 0)
        # String index 1 uses the pool's position-dependent byte mask.
        data = bytes([1, 2, 185, 60, 2, 2, 52, 50, 1, 0])
        self.assertEqual(list(_constants(data)), [('string', b'ok'), ('number', b'42'), ('string', b'')])
        with self.assertRaises(ValueError):
            list(_constants(b'\x01\x03x'))

    def test_key_arithmetic_and_environment_assumption(self):
        source = ',((function() local a=17 a=(a*5+(unfnC)*9+7)%2147483647 a=(a*3+(m0XTmwd[1]:byte(2))*2+4)%2147483647 return a+(-4) end)()),12)'
        key, offset, end = _seed(source, 0)
        self.assertEqual((key, offset, end), ((17 * 5 + 7) * 3 + 124 * 2, 12, len(source)))

    @unittest.skipUnless(shutil.which('lua5.1'), 'Lua 5.1 is needed for the authored fixture')
    def test_experimental_unpack_matches_authored_fixture(self):
        # This is a small self-authored program with data only; no submitted Lua is executed.
        fixture = r"""
local unfnC=0
local function bytes(s,k,c)
  local sum=0
  for i=1,#s do sum=(sum+i*s:byte(i))%2147483647 end
  local a=(k+sum-c)%2147483646
  local b=((k+sum-c)*40503+1013904223)%2147483646
  if a==0 then a=2147483646 end
  if b==0 then b=2147483646 end
  local out={}
  for i=1,#s do
    a=a*16807%2147483647 b=b*48271%2147483647
    local p=(a%65536)*(b%32768)
    out[i]=(s:byte(i)-p-math.floor(p/256)-math.floor(p/65536))%256
  end
  return out
end
local function l00oIo(s,k,c)
  return bytes(s,k,c)
end
local function J47b09niogn(s,k,c)
  local data=bytes(s,k,c)
  return setmetatable({}, {__index=function(_,i)
    return string.char((data[3]-(195*i+147+244*i))%256)
  end})
end
local code=l00oIo("\000\002",((function() local a=17 a=(a*5+(unfnC)*9+7)%2147483647 return a+(3) end)()),12)
local constants=J47b09niogn("\000\085\215",((function() local a=17 a=(a*5+(unfnC)*9+7)%2147483647 return a+(3) end)()),12)
print(code[1],code[2],constants[1])
"""
        with patch('aryq_inspector._BLOCKS', 1):
            report = _inspect(fixture)
        original = subprocess.run(['lua5.1', '-'], input=fixture.encode(), capture_output=True, timeout=3)
        unpacked = subprocess.run(['lua5.1', '-'], input=report.unpacked.encode(), capture_output=True, timeout=3)
        self.assertEqual((original.returncode, original.stdout), (0, b'119\t15\tX\n'))
        self.assertEqual((unpacked.returncode, unpacked.stdout), (original.returncode, original.stdout))
        self.assertEqual((report.integers, report.strings), (2, 1))

    def test_unknown_source_is_not_identified_and_busy_is_bounded(self):
        self.assertIsNone(inspect_aryq('different Lua'))
        with patch('aryq_inspector._SAMPLE_SHA256', '2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881'):
            with patch('aryq_inspector._LOCK.acquire', return_value=False):
                self.assertEqual(inspect_aryq('x'), 'busy')

if __name__ == '__main__':
    unittest.main()
