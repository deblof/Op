import shutil
import subprocess
import unittest
from unittest.mock import patch
from static_cleanup import cleanup, _XOR_HELPER
from cleanup_runner import analyze_source

class CleanupTests(unittest.TestCase):
    def test_literal_string_bytes_and_utf8_remain_exact(self):
        source = 'print("\\112\\114\\105\\110\\116", "é", "\\195\\169", "\\255", "\\0001")'
        result = cleanup(source)
        self.assertIn('"print"', result['source'])
        self.assertIn('"\\195\\169"', result['source'])
        self.assertIn('"\\255"', result['source'])
        self.assertIn('"\\0001"', result['source'])
        self.assertGreater(result['changes'], 0)
        self.compare(source, result['source'])

    def compare(self, before, after):
        exe = shutil.which('lua5.1')
        if not exe:
            self.skipTest('Lua 5.1 is needed for authored behavior comparisons')
        a = subprocess.run([exe, '-'], input=before.encode(), capture_output=True, timeout=3)
        b = subprocess.run([exe, '-'], input=after.encode(), capture_output=True, timeout=3)
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual((b.returncode,b.stdout), (a.returncode,a.stdout), b.stderr)

    def test_arithmetic_precedence_comments_and_multiple_returns(self):
        source = '-- start\nlocal function f() return 1,2 end; local n=(4+1)*3; print(n, -2^2, f());print((1-2)-3, "a" .. "b", #("é")); --tail'
        result = cleanup(source)
        self.assertIn('-- start', result['source'])
        self.assertIn('--tail', result['source'])
        self.assertIn('"ab"', result['source'])
        self.compare(source, result['source'])

    def test_short_circuit_calls_and_builtin_overrides_are_not_folded(self):
        source = 'local string={char=function() return "overridden" end}; print(string.char(65)); local x=nil; print(x and missing())'
        output = cleanup(source)
        self.assertIn('string.char(65)', output['source'])
        self.assertIn('"A"', output['candidates'])
        self.compare(source, output['source'])

    def test_repeating_xor_is_only_a_candidate(self):
        source = 'local xor=' + _XOR_HELPER.removeprefix('return ') + '; print(xor("ih", "!"))'
        result = cleanup(source)
        self.assertIn('"HI"', result['xor_candidates'])
        self.assertIn('xor("ih", "!")', result['source'])

    def test_signed_zero_and_division_by_zero_are_not_folded(self):
        source = 'print(1/(-0),0/0,1/0,1e100+1)'
        result = cleanup(source)
        self.assertIn('1/(-0)', result['source'])
        self.compare(source, result['source'])

    def test_comments_strings_and_statement_boundaries(self):
        source = 'local function f() return "--not a comment" end;print(f());local a=[=[long\nstring]=];print(a)'
        self.compare(source, cleanup(source)['source'])

    def test_size_syntax_and_busy_fail_closed(self):
        self.assertIsNone(analyze_source('x'*200001))
        self.assertIsNone(analyze_source('local function'))
        with patch('cleanup_runner._BUSY') as gate:
            gate.acquire.return_value=False
            self.assertEqual(analyze_source('print(1)'), 'busy')
