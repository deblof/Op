import shutil
import subprocess
import unittest
from unittest.mock import patch

from prometheus_runner import analyze_dispatcher
from prometheus_transform import flatten_dispatcher, MAX_SOURCE_BYTES

# Written for this test; no submitted code is executed.
PROGRAM = ('local function vm(p,a,u,g) local r while p do '
           'if p<2 then r={"a"} p=nil else if p<4 then r={"b"} p=nil '
           'else r={"c"} p=nil end end end return unpack(r) end '
           'print(vm(3,{},{},{}))' + ' ' * 1000)


class DispatcherTests(unittest.TestCase):
    def test_transform_preserves_original_leaves_and_output(self):
        output, count = flatten_dispatcher(PROGRAM)
        self.assertEqual(count, 3)
        self.assertIn('elseif p < 4 then', output)
        self.assertIn('\nelseif p < 4 then\n', output)
        if shutil.which('lua5.1'):
            original = subprocess.run(['lua5.1', '-'], input=PROGRAM.encode(), capture_output=True, timeout=3)
            partial = subprocess.run(['lua5.1', '-'], input=output.encode(), capture_output=True, timeout=3)
            self.assertEqual((original.returncode, original.stdout), (0, b'b\n'))
            self.assertEqual((partial.returncode, partial.stdout), (original.returncode, original.stdout))

    def test_rejects_unordered_bounds_and_oversized(self):
        self.assertIsNone(flatten_dispatcher(PROGRAM.replace('p<4', 'p<1')))
        self.assertIsNone(flatten_dispatcher('x' * (MAX_SOURCE_BYTES + 1)))

    def test_leading_comments_and_trailing_newlines_are_preserved(self):
        decorated = '-- first comment\n  -- second comment\r\n\n' + PROGRAM + '\r\n'
        expected, _ = flatten_dispatcher(PROGRAM)
        result = analyze_dispatcher(decorated)
        self.assertIsNotNone(result)
        self.assertEqual(result, ('-- first comment\n  -- second comment\r\n\n' + expected + '\r\n', 3))
        if shutil.which('lua5.1'):
            original = subprocess.run(['lua5.1', '-'], input=decorated.encode(), capture_output=True, timeout=3)
            partial = subprocess.run(['lua5.1', '-'], input=result[0].encode(), capture_output=True, timeout=3)
            self.assertEqual((partial.returncode, partial.stdout), (original.returncode, original.stdout))

    def test_multiline_unicode_and_comments_preserve_authored_behavior(self):
        source = ('--[[comment with end/else\nUnicode é]]\n' + PROGRAM.replace(
            'then r=', 'then -- leaf\n r=').replace('p=nil else', 'p=nil\nelse') + '\nprint("after")')
        output, count = flatten_dispatcher(source)
        self.assertEqual(count, 3)
        self.assertIn('Unicode é', output)
        self.assertIn('print("after")', output)
        if shutil.which('lua5.1'):
            original = subprocess.run(['lua5.1', '-'], input=source.encode(), capture_output=True, timeout=3)
            partial = subprocess.run(['lua5.1', '-'], input=output.encode(), capture_output=True, timeout=3)
            self.assertEqual((original.returncode, original.stdout), (0, b'b\nafter\n'))
            self.assertEqual((partial.returncode, partial.stdout), (original.returncode, original.stdout))

    def test_isolated_worker_detects_and_bounds(self):
        result = analyze_dispatcher(PROGRAM)
        self.assertIsNotNone(result)
        self.assertEqual(result[1], 3)
        self.assertIsNone(analyze_dispatcher('x' * (MAX_SOURCE_BYTES + 1)))
        with patch('prometheus_runner._BUSY') as busy:
            busy.acquire.return_value = False
            self.assertEqual(analyze_dispatcher(PROGRAM), 'busy')

    def test_rejects_invalid_source_without_crashing(self):
        self.assertIsNone(analyze_dispatcher('local function x() while ' + 'a'*1000))
