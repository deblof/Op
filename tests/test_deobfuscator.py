import unittest

from deobfuscator import MAX_BYTES, MAX_INPUT_BYTES, deobfuscate, remote_url


class DecoderTests(unittest.TestCase):
    def test_literal_wrappers_and_lua_escapes(self):
        cases = [
            ('return loadstring("\\112\\114\\105\\110\\116(\\\"ok\\\")")()', 'print("ok")'),
            ("load('\\x70\\x72' .. 'int' .. string.char(40, 0x31, 41))", 'print(1)'),
            ('loadstring([=[\nprint("hi")]=])()', 'print("hi")'),
            ('loadstring([[\r\na\r\nb\rc]])', 'a\nb\nc'),
            ('loadstring("\\195\\169")', 'é'),
            ('loadstring("a\\z \n b")', 'ab'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(deobfuscate(source).source, expected)
                self.assertEqual(deobfuscate(source).layers, 1)

    def test_variables_and_standard_string_operations(self):
        cases = [
            ("local a = 'pri'; local b = 'nt'; return loadstring(a .. b .. '(1)')()", 'print(1)'),
            ('loadstring(string.reverse(")1(tnirp"))()', 'print(1)'),
            ('loadstring(table.concat({"print", "(1)"}))()', 'print(1)'),
            ('loadstring(table.concat({"print", "1", "end",}, ";"))()', 'print;1;end'),
            ('loadstring(string.rep("a", 3, "-"))()', 'a-a-a'),
            ('loadstring(string.rep("a", 0))()', ''),
            ('loadstring(base64_decode("cHJpbnQoMSk="))()', 'print(1)'),
            ('local code = base64.decode("cHJpbnQoMSk="); return loadstring(code)()', 'print(1)'),
            ('loadstring(syn.crypt.base64.decode("cHJpbnQoMSk="))()', 'print(1)'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                result = deobfuscate(source)
                self.assertEqual((result.source, result.layers), (expected, 1))

    def test_new_patterns_reject_dynamic_and_oversized_inputs(self):
        cases = [
            'local a = os.execute("bad"); loadstring(a)()',
            'local a = "print(1)"; a = "print(2)"; loadstring(a)()',
            'local string = "x"; loadstring(string.reverse("abc"))()',
            'local loadstring = "x"; loadstring("abc")()',
            'loadstring(base64_decode("bad!"))()',
            'loadstring(base64_decode("/w=="))()',
            'loadstring(string.rep("a", 1000001))()',
            'loadstring(string.rep("a", 1000000, "x"))()',
            'loadstring(table.concat({"a", os.execute("bad")}))()',
            'loadstring(unknown("cHJpbnQoMSk="))()',
        ]
        for source in cases:
            with self.subTest(source=source):
                result = deobfuscate(source)
                self.assertEqual((result.source, result.layers), (source, 0))

    def test_new_patterns_respect_recursion_and_local_limits(self):
        nested = '"a"'
        for _ in range(70):
            nested = 'string.reverse(' + nested + ')'
        self.assertEqual(deobfuscate('loadstring(' + nested + ')').layers, 0)
        locals_source = ';'.join('local a%d="x"' % i for i in range(129))
        self.assertEqual(deobfuscate(locals_source + ';loadstring(a0)').layers, 0)
        self.assertEqual(deobfuscate('local a=string.rep("x",1000000);local b=a;local c=a;loadstring(c)').layers, 0)

    def test_hex_and_byte_xor(self):
        cases = [
            ('loadstring(hex_decode("7072696e74283129"))()', 'print(1)'),
            ('loadstring(crypt.hexdecode("7072696E74283129"))()', 'print(1)'),
            ('loadstring(string.char(bit32.bxor(112, 0), bit.bxor(114, 0), 105, 110, 116, 40, 49, 41))()', 'print(1)'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(deobfuscate(source).source, expected)
                self.assertEqual(deobfuscate(source).layers, 1)
        for source in ('loadstring(hex_decode("z1"))', 'loadstring(hex_decode("f"))',
                       'loadstring(string.char(bit32.bxor(256, 1)))',
                       'loadstring(string.char(bit32.bxor(os.execute("bad"), 1)))'):
            with self.subTest(source=source):
                self.assertEqual(deobfuscate(source).layers, 0)

    def test_remote_wrapper_parser(self):
        url = 'https://raw.githubusercontent.com/owner/repo/main/test.lua'
        self.assertEqual(remote_url(url), url)
        self.assertEqual(remote_url('return loadstring(game:HttpGet("' + url + '"))()'), url)
        self.assertEqual(remote_url('load(game:HttpGetAsync("' + url + '"))'), url)
        self.assertEqual(remote_url('loadstring(game:HttpGet("' + url + '", true))()'), url)
        self.assertIsNone(remote_url('loadstring(game:HttpGet("' + url + '", os.execute("bad")))()'))
        self.assertIsNone(remote_url('loadstring(game:HttpGet(os.execute("bad")))()'))
        self.assertIsNone(remote_url('loadstring(game:HttpGet("' + url + '"))(); print(1)'))

    def test_nested_layers_and_limit(self):
        source = "loadstring(\"loadstring('print(1)')()\")()"
        self.assertEqual(deobfuscate(source).source, 'print(1)')
        self.assertEqual(deobfuscate(source).layers, 2)
        # Long-bracket delimiters allow straightforward nesting.
        nested = 'print(1)'
        for i in range(9):
            eq = '=' * i
            nested = f'loadstring([{eq}[{nested}]{eq}])()'
        result = deobfuscate(nested)
        self.assertEqual(result.layers, 8)
        self.assertEqual(deobfuscate(result.source).source, 'print(1)')

    def test_unsupported_inputs_are_not_evaluated_or_partially_unwrapped(self):
        cases = [
            'loadstring(os.execute("bad"))()',
            'print(1); loadstring("print(2)")()',
            'loadstring("print(1)"); print(2)',
            'loadstring("\\256")()',
            'loadstring("\\xQQ")()',
            'loadstring("\\255")()',
            'loadstring(string.char(256))()',
            'loadstring("unterminated)',
            'loadstring("ok", "chunkname")',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(deobfuscate(source).source, source)
                self.assertEqual(deobfuscate(source).layers, 0)

    def test_limits_and_nesting(self):
        with self.assertRaises(ValueError):
            deobfuscate('a' * (MAX_INPUT_BYTES + 1))
        source = 'loadstring(' + '(' * 70 + '"ok"' + ')' * 70 + ')'
        self.assertEqual(deobfuscate(source).layers, 0)


if __name__ == '__main__':
    unittest.main()
