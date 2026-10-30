import unittest
from unittest.mock import patch

from test_patterns import SAMPLE
from vm_formats import analyze_vm
from vm_inspector import VMReport


class VMFormatTests(unittest.TestCase):
    def test_known_interpreter_routes_to_existing_analyzer(self):
        report = VMReport('report', 1, 2, 3, False)
        with patch('vm_formats.inspect_vm', return_value=report):
            result = analyze_vm('anything')
        self.assertEqual(result.kind, 'report')
        self.assertIs(result.report, report)

    def test_literal_helper_routes_to_partial_decoder(self):
        result = analyze_vm(SAMPLE)
        self.assertEqual(result.kind, 'partial')
        self.assertEqual(result.count, 2)
        self.assertIn('"ssu"', result.source)

    def test_header_is_only_a_hint_and_no_decoder_is_claimed(self):
        result = analyze_vm('-- This file was protected using Luraph Obfuscator v15.0 [https://lura.ph/]\nreturn 1')
        self.assertEqual(result.kind, 'unsupported')
        self.assertEqual(result.label, 'Luraph v15.0')
        self.assertIsNone(result.source)
        self.assertIsNone(analyze_vm('print("Luraph Obfuscator v15.0")'))
        self.assertIsNone(analyze_vm('print(1)'))

    def test_translation_and_failure_fallback(self):
        report = VMReport('report',1,2,3,False)
        with patch('vm_formats.inspect_vm', return_value=report):
            with patch('vm_formats.lift_gist', return_value=('return 1',1,2)):
                result = analyze_vm('source')
                self.assertEqual((result.kind,result.source,result.structured), ('translated','return 1',1))
            with patch('vm_formats.lift_gist', side_effect=ValueError('unsupported control flow')):
                result = analyze_vm('source')
                self.assertEqual(result.kind,'report')
            with patch('vm_formats._GIST_LIFT.acquire', return_value=False):
                self.assertEqual(analyze_vm('source').kind,'busy')
