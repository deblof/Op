import unittest

from patterns import decode_addition_table

SAMPLE = '''-- example
local marker="abc"
local function add(a,b)local t={}for i=1,#a do t[i]=string.char((string.byte(a,i)+string.byte(b,((i%#b))+1))%256)end return table.concat(t) end;local values={add("ABC","12"),add("\\000\\255","A")}; print(values[1])'''


class PatternTests(unittest.TestCase):
    def test_literal_table_uses_lua_indexing_and_byte_escapes(self):
        output, count = decode_addition_table(SAMPLE)
        self.assertEqual(count, 2)
        self.assertIn('local values={"ssu","A@"}', output)
        self.assertTrue(output.endswith('; print(values[1])'))

    def test_unrecognized_or_dynamic_table_is_unchanged(self):
        for source in (SAMPLE.replace('+string.byte', '-string.byte'),
                       SAMPLE.replace('add("ABC","12")', 'add(get_data(),"12")'),
                       SAMPLE.replace('local marker=', 'string.char=evil; local marker='),
                       SAMPLE.replace('"12"', '""'),
                       SAMPLE.replace('local values=', 'local string=')):
            with self.subTest(source=source):
                self.assertEqual(decode_addition_table(source), (source, 0))

    def test_non_ascii_and_special_bytes_are_escaped(self):
        source = SAMPLE.replace('add("ABC","12")', 'add("\\000\\001\\002","\\034")')
        output, count = decode_addition_table(source)
        self.assertEqual(count, 2)
        self.assertIn('"\\034#$"', output)
