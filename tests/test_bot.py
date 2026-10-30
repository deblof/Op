import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot import bot, deblof, help_command


def interaction():
    return SimpleNamespace(
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def test_registered_commands(self):
        self.assertEqual({c.name for c in bot.tree.get_commands()}, {'help', 'deblof', 'strings', 'analyze', 'format'})

    async def test_help_is_private(self):
        ctx = interaction()
        await help_command.callback(ctx)
        self.assertTrue(ctx.response.send_message.await_args.kwargs['ephemeral'])
        self.assertIn('string.char', ctx.response.send_message.await_args.args[0])
        self.assertIn('Base64', ctx.response.send_message.await_args.args[0])

    async def test_source_decodes_to_private_attachment(self):
        ctx = interaction()
        await deblof.callback(ctx, source='loadstring("print(1)")()')
        ctx.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        kwargs = ctx.followup.send.await_args.kwargs
        self.assertTrue(kwargs['ephemeral'])
        self.assertEqual(kwargs['file'].fp.read(), b'print(1)')

    async def test_new_decoder_patterns_via_command(self):
        ctx = interaction()
        source = 'local code = base64_decode("cHJpbnQoMSk="); return loadstring(code)()'
        await deblof.callback(ctx, source=source)
        self.assertEqual(ctx.followup.send.await_args.kwargs['file'].fp.read(), b'print(1)')

    async def test_remote_source_command(self):
        ctx = interaction()
        source = 'return loadstring(game:HttpGet("https://raw.githubusercontent.com/a/b/c/d.lua"))()'
        with patch('bot.fetch_source', new=AsyncMock(return_value='loadstring("print(1)")()')) as fetch:
            await deblof.callback(ctx, source=source)
        fetch.assert_awaited_once_with('https://raw.githubusercontent.com/a/b/c/d.lua')
        self.assertEqual(ctx.followup.send.await_args.kwargs['file'].fp.read(), b'print(1)')
        self.assertIn('Downloaded public source', ctx.followup.send.await_args.args[0])

    async def test_unrecognized_remote_source_is_labeled_original(self):
        ctx = interaction()
        source = 'loadstring(game:HttpGet("https://raw.githubusercontent.com/a/b/c/d.lua"))()'
        with patch('bot.fetch_source', new=AsyncMock(return_value='return unknown_vm()')):
            await deblof.callback(ctx, source=source)
        args = ctx.followup.send.await_args
        self.assertIn('returned unchanged', args.args[0])
        self.assertEqual(args.kwargs['file'].filename, 'original.lua')
        self.assertEqual(args.kwargs['file'].fp.read(), b'return unknown_vm()')
        self.assertTrue(args.kwargs['ephemeral'])

    async def test_remote_download_error_is_private(self):
        from remote import DownloadError
        ctx = interaction()
        with patch('bot.fetch_source', new=AsyncMock(side_effect=DownloadError('Not allowed'))):
            await deblof.callback(ctx, source='https://raw.githubusercontent.com/a/b/main/x.lua')
        self.assertEqual(ctx.followup.send.await_args.args[0], 'Not allowed')
        self.assertTrue(ctx.followup.send.await_args.kwargs['ephemeral'])

    async def test_partial_vm_report_is_private_and_keeps_source(self):
        from vm_inspector import VMReport
        ctx = interaction()
        report = VMReport('Partial report', 2, 3, 4, False)
        with patch('vm_formats.inspect_vm', return_value=report):
            await deblof.callback(ctx, source='virtualized original')
        kwargs = ctx.followup.send.await_args.kwargs
        self.assertTrue(kwargs['ephemeral'])
        self.assertIn('not been reconstructed', ctx.followup.send.await_args.args[0])
        self.assertEqual([item.filename for item in kwargs['files']], ['vm-analysis.txt', 'original.lua'])
        self.assertEqual(kwargs['files'][0].fp.read(), b'Partial report')
        self.assertEqual(kwargs['files'][1].fp.read(), b'virtualized original')

    async def test_exact_aryq_report_keeps_all_files_private(self):
        from aryq_inspector import AryqReport
        from vm_formats import VMAnalysis
        ctx = interaction()
        report = AryqReport('candidate constants', 1, 2, 1, 3, 4, False, 'unpacked vm')
        result = VMAnalysis('aryq', 'exact sample', report=report, source='partial', count=7)
        with patch('bot.analyze_vm', return_value=result):
            await deblof.callback(ctx, source='original')
        args = ctx.followup.send.await_args
        self.assertTrue(args.kwargs['ephemeral'])
        self.assertIn('may behave differently', args.args[0])
        files = args.kwargs['files']
        self.assertEqual([f.filename for f in files], ['aryq-analysis.txt', 'partially-decoded.lua',
                                                       'experimental-unpacked-vm.lua', 'original.lua'])
        self.assertEqual([f.fp.read() for f in files], [b'candidate constants', b'partial', b'unpacked vm', b'original'])

    async def test_translated_source_keeps_original_and_report_private(self):
        from vm_inspector import VMReport
        from vm_formats import VMAnalysis
        ctx = interaction()
        result = VMAnalysis('translated', 'known VM', VMReport('report',2,3,4,False),
                            'return 42', count=2, structured=1, runtime_assumption=True)
        with patch('bot.analyze_vm', return_value=result):
            await deblof.callback(ctx, source='original')
        args = ctx.followup.send.await_args
        self.assertTrue(args.kwargs['ephemeral'])
        self.assertIn('Translated 2', args.args[0])
        self.assertIn('standard-runtime keys', args.args[0])
        self.assertEqual([f.filename for f in args.kwargs['files']],
                         ['readable.lua', 'aryq-analysis.txt', 'original.lua'])
        self.assertEqual([f.fp.read() for f in args.kwargs['files']], [b'return 42', b'report', b'original'])

    async def test_pastebin_wrapper_is_downloaded(self):
        ctx = interaction()
        url = 'https://pastebin.com/Ab12Cd34'
        with patch('bot.fetch_source', AsyncMock(return_value='loadstring("print(1)")()')) as fetch:
            await deblof.callback(ctx, source=f'loadstring(game:HttpGet("{url}"))()')
        fetch.assert_awaited_once_with('https://pastebin.com/raw/Ab12Cd34')
        self.assertEqual(ctx.followup.send.await_args.kwargs['file'].fp.read(), b'print(1)')

    async def test_aryq_table_is_reported_as_partial(self):
        from test_patterns import SAMPLE
        ctx = interaction()
        await deblof.callback(ctx, source=SAMPLE)
        self.assertIn('Partially decoded 2', ctx.followup.send.await_args.args[0])
        self.assertIn('not been reconstructed', ctx.followup.send.await_args.args[0])
        self.assertEqual([f.filename for f in ctx.followup.send.await_args.kwargs['files']], ['partially-decoded.lua', 'original.lua'])

    async def test_unhandled_luraph_header_is_not_claimed_as_decoded(self):
        ctx = interaction()
        await deblof.callback(ctx, source='-- This file was protected using Luraph Obfuscator v15.0 [https://lura.ph/]\nreturn 1')
        msg = ctx.followup.send.await_args.args[0]
        self.assertIn('Luraph v15.0', msg)
        self.assertIn('does not verify', msg)
        self.assertIn('No decoder', msg)
        self.assertNotIn('file', ctx.followup.send.await_args.kwargs)

    async def test_experimental_vm_keeps_original(self):
        from test_prometheus import PROGRAM
        ctx = interaction()
        await deblof.callback(ctx, source=PROGRAM)
        message = ctx.followup.send.await_args.args[0]
        files = ctx.followup.send.await_args.kwargs['files']
        self.assertIn('Experimental partial VM cleanup', message)
        self.assertIn('may behave differently', message)
        self.assertEqual([f.filename for f in files], ['partial-vm.lua', 'original.lua'])
        self.assertEqual(files[1].fp.read().decode(), PROGRAM)

    async def test_attachment_and_unsupported_content(self):
        ctx = interaction()
        file = SimpleNamespace(size=17, read=AsyncMock(return_value=b'load("print(1)")'))
        await deblof.callback(ctx, file=file)
        self.assertEqual(ctx.followup.send.await_args.kwargs['file'].fp.read(), b'print(1)')
        ctx = interaction()
        await deblof.callback(ctx, source='print(1)')
        self.assertIn('No supported', ctx.followup.send.await_args.args[0])
        self.assertTrue(ctx.followup.send.await_args.kwargs['ephemeral'])

    async def test_diagnostic_commands_are_private_and_keep_original(self):
        from bot import strings_command, analyze_command, format_command
        for command in (strings_command, analyze_command, format_command):
            ctx = interaction()
            await command.callback(ctx, source='print("\\065", (2+3))')
            args = ctx.followup.send.await_args
            self.assertTrue(args.kwargs['ephemeral'])
            if command is format_command:
                self.assertEqual([f.filename for f in args.kwargs['files']], ['formatted.lua', 'original.lua'])
                self.assertEqual(args.kwargs['files'][1].fp.read(), b'print("\\065", (2+3))')
            else:
                report = args.kwargs['file'].fp.read().decode()
                self.assertIn('"A"', report)
                if command is analyze_command:
                    self.assertIn('SHA-256:', report)

    async def test_unknown_vm_gets_literal_cleanup_without_success_claim(self):
        ctx = interaction()
        await deblof.callback(ctx, source='unknown_vm("\\104\\105", (10+2))')
        args = ctx.followup.send.await_args
        self.assertIn('Partial static cleanup', args.args[0])
        self.assertIn('has not been reconstructed', args.args[0])
        self.assertEqual([f.filename for f in args.kwargs['files']], ['partially-decoded.lua', 'original.lua', 'strings.txt'])

    async def test_busy_request_does_not_block_help_or_start_second_analysis(self):
        import asyncio
        started, release = asyncio.Event(), asyncio.Event()
        async def read():
            started.set()
            await release.wait()
            return b'loadstring("print(1)")()'
        first, second, help_ctx = interaction(), interaction(), interaction()
        task = asyncio.create_task(deblof.callback(first, file=SimpleNamespace(size=25, read=read)))
        await started.wait()
        try:
            await deblof.callback(second, source='print(1)')
            self.assertIn('another request', second.response.send_message.await_args.args[0])
            await help_command.callback(help_ctx)
            self.assertTrue(help_ctx.response.send_message.await_args.kwargs['ephemeral'])
        finally:
            release.set()
            await task

    async def test_invalid_arguments_and_file(self):
        for args in ({}, {'source': 'x', 'file': SimpleNamespace(size=1)},
                     {'file': SimpleNamespace(size=2_000_001)}):
            ctx = interaction()
            await deblof.callback(ctx, **args)
            self.assertTrue(ctx.response.send_message.await_args.kwargs['ephemeral'])
        ctx = interaction()
        file = SimpleNamespace(size=1, read=AsyncMock(return_value=b'\xff'))
        await deblof.callback(ctx, file=file)
        self.assertIn('UTF-8', ctx.followup.send.await_args.args[0])


if __name__ == '__main__':
    unittest.main()
