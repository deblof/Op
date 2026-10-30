import hashlib
import struct
import unittest
from unittest.mock import patch

from vm_inspector import (Function, InvalidVM, Reader, _ROOT_CONSTANTS, _ROOT_SETUP,
                          inspect_vm, recover_strings, render, unpack_payload)


def u32(value):
    return struct.pack('<I', value)


def fixture():
    # Four constants, three instruction slots, and one empty child function.
    return (u32(4) + b'\x03' + u32(5) + b'print' + b'\x01\x01' + b'\x02'
            + struct.pack('<d', 3.5) + b'\x00' + b'\x01' + u32(3)
            + b'\x08' + struct.pack('<HHHH', 9, 1, 2, 3)
            + b'\x02' + struct.pack('<HHI', 68, 0, 2) + b'\x01'
            + u32(1) + u32(0) + b'\x00' + u32(0) + u32(0))


class VMTests(unittest.TestCase):
    def test_unpacks_run_length_and_rejects_bad_input(self):
        self.assertEqual(unpack_payload('3Q00FF'), b'\x00\x00\x00\xff')
        for encoded in ('1', 'AQ00', '2Q', '2Q3Q00', 'GG', '9Q' * 1_000_001):
            with self.subTest(encoded=encoded[:12]), self.assertRaises(InvalidVM):
                unpack_payload(encoded)

    def test_static_disassembly_of_binary_format(self):
        reader = Reader(fixture())
        root = reader.function()
        self.assertEqual(reader.pos, len(fixture()))
        report = render(root, reader)
        self.assertEqual((report.functions, report.instructions, report.constants), (2, 3, 4))
        self.assertIn("K1 = b'print'", report.text)
        self.assertIn('K3 = 3.5', report.text)
        self.assertIn('OP_9 LOAD_OPERAND A=K1 B=2 C=3', report.text)
        self.assertIn('OP_68 JUMP A=0 B=2 C=-', report.text)
        self.assertIn('<empty slot>', report.text)
        self.assertIn('FUNCTION 0.0', report.text)
        self.assertIn('not reconstructed runnable Lua', report.text)

    def test_fingerprint_and_payload_validation(self):
        prefix = '-- recognized interpreter\n'
        digest = hashlib.sha256(prefix.encode()).hexdigest()
        source = prefix + 'return v15("LOL!' + fixture().hex().upper() + '",v9(),...);'
        self.assertIsNone(inspect_vm(source))
        with patch('vm_inspector._INTERPRETER_HASH', digest):
            self.assertIsNotNone(inspect_vm(source))
            self.assertIsNone(inspect_vm(source + 'extra'))
            self.assertIsNone(inspect_vm(source.replace('LOL!', 'LOL!GG')))
            self.assertIsNone(inspect_vm(source.replace('LOL!', 'LOL!FF')))

    def test_verified_xor_strings_and_captured_decoder(self):
        decoder = Function(2, [1.0, 256.0], [], [])
        digest = hashlib.sha256(repr((decoder.params, decoder.constants, decoder.instructions)).encode()).hexdigest()
        cipher = b'\xe1\xcf\xda<\xe3\xa9\xd4'
        key = b'~\xb1\xa3\xbbE\x86\xdb\xa7'
        child = Function(0, [cipher, key], [
            (83, 8, 0, 0, 0), (9, 9, 1, None, 2),
            (9, 10, 2, None, 2), (143, 8, 10, 0, 0),
        ], [])
        root = Function(0, _ROOT_CONSTANTS + [cipher, key], _ROOT_SETUP + [
            (86, 8, 7, 0, 0), (9, 9, 11, None, 2),
            (9, 10, 12, None, 2), (143, 8, 10, 0, 0),
            (136, 20, 1, 1, 0), (128, 0, 7, 0, 0),
        ], [decoder, child])
        with patch('vm_inspector._DECODER_SIG', digest):
            values = recover_strings(root)
            self.assertEqual(values, [('0', 23, b'Players'), ('0.1', 1, b'Players')])
            child.instructions.append((30, 1, 0, 0, 0))
            self.assertEqual(recover_strings(root), [('0', 23, b'Players')])
            root.instructions.append((9, 7, 11, None, 2))
            self.assertEqual(recover_strings(root), [])
        self.assertEqual(recover_strings(root), [])

    def test_limits_and_truncation(self):
        with self.assertRaises(InvalidVM):
            Reader(u32(20_001)).function()
        with self.assertRaises(InvalidVM):
            Reader(fixture()[:-1]).function()
        reader = Reader(fixture())
        root = reader.function()
        with patch('vm_inspector._MAX_REPORT', 256):
            report = render(root, reader)
        self.assertTrue(report.truncated)
        self.assertIn('truncated', report.text)


if __name__ == '__main__':
    unittest.main()
