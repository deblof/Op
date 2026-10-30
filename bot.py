"""Discord slash commands for statically decoding supported Lua wrappers."""

import asyncio
import hashlib
import io
import logging
import os

import discord
from discord import app_commands

from deobfuscator import MAX_INPUT_BYTES, MAX_LAYERS, Result
from remote import DownloadError, fetch_source
from vm_formats import analyze_vm
from cleanup_runner import analyze_source
from pipeline import resolve_source

HELP = (
    "`/deblof` decodes supported wrappers and translates recognized VMs. "
    "`/strings` extracts literal strings and candidate decoded strings. "
    "`/analyze` reports source size, fingerprints and available analysis. "
    "`/format` formats supported Lua syntax without running it. "
    "All commands accept either a UTF-8 Lua file (2 MB maximum) or pasted source/link. "
    "Links: GitHub raw/blob, Gist raw, GitLab raw/blob, Pastebin, Pastefy and jsDelivr GitHub. "
    "Up to 5 linked loaders and 32 wrapper layers per source; string.char, byte XOR, "
    "string.sub/reverse/rep, concatenation, Base64 and hex wrappers are supported. "
    "Literal-only cleanup decodes string escapes and folds constant arithmetic. "
    "Formatting and string discovery are bounded to 200 KB; dispatcher cleanup to 100 KB. "
    "Known Gist VM and exact Aryq sample have translators; Prometheus-style dispatchers "
    "have partial cleanup. Unknown VMs, including Luraph, may remain obfuscated. "
    "Original names cannot be recovered. Candidate strings assume standard builtins; "
    "XOR guesses may be unrelated calls. Originals are retained for partial outputs. "
    "All analysis is static, submitted Lua is never run, and responses are private."
)
_JOBS = asyncio.Semaphore(1)


class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild_id = os.getenv('DISCORD_GUILD_ID')
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logging.info('Synced commands to the configured guild')
        else:
            await self.tree.sync()
            logging.info('Synced global commands')


bot = Bot()


@bot.tree.command(name='help', description='Show supported Lua deobfuscation features')
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(HELP, ephemeral=True)


@bot.tree.command(name='deblof', description='Decode Lua wrappers or translate a recognized Lua VM')
@app_commands.checks.cooldown(1, 5.0, key=lambda interaction: interaction.user.id)
@app_commands.describe(file='A UTF-8 Lua file, up to 2 MB', source='Lua source or a supported public source link')
async def deblof(interaction: discord.Interaction, file: discord.Attachment | None = None,
                 source: str | None = None):
    if _JOBS.locked():
        await interaction.response.send_message('The bot is processing another request. Please try again shortly.', ephemeral=True)
        return
    async with _JOBS:
        await run_deblof(interaction, file, source)


async def run_deblof(interaction, file=None, source=None):
    if (file is None) == (source is None):
        await interaction.response.send_message('Provide exactly one of `file` or `source`.', ephemeral=True)
        return
    if file is not None and file.size > MAX_INPUT_BYTES:
        await interaction.response.send_message('The file exceeds the 2 MB limit.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        if file is not None:
            data = await file.read()
            if len(data) > MAX_INPUT_BYTES:
                raise ValueError('The file exceeds the 2 MB limit.')
            source = data.decode('utf-8-sig')
        resolved = await asyncio.wait_for(resolve_source(source, fetcher=fetch_source), timeout=45)
        result = Result(resolved.source, resolved.layers)
        decoded_layers = resolved.layers
        reached_limit = resolved.limited
        fetched = resolved.downloads > 0
    except asyncio.TimeoutError:
        await interaction.followup.send('The source download chain took too long. Please try a direct raw file link.', ephemeral=True)
        return
    except DownloadError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    except UnicodeDecodeError:
        await interaction.followup.send('The file must be UTF-8 encoded.', ephemeral=True)
        return
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    except discord.HTTPException:
        await interaction.followup.send('Could not download the attachment. Please try again.', ephemeral=True)
        return
    analysis = await asyncio.to_thread(analyze_vm, result.source)
    if analysis and analysis.kind == 'translated':
        message = (f'Translated {analysis.count} VM functions into Lua with explicit operations. '
                   f'{analysis.structured} functions have structured control flow; '
                   'other functions retain explicit state blocks. Original variable names are unavailable. '
                   'Keep the original and test the recovered Lua in your environment.')
        if analysis.runtime_assumption:
            message += ' This Aryq result assumes the standard-runtime keys.'
        filename = 'aryq-analysis.txt' if analysis.runtime_assumption else 'vm-analysis.txt'
        files = [discord.File(io.BytesIO(analysis.source.encode('utf-8')), filename='readable.lua'),
                 discord.File(io.BytesIO(analysis.report.text.encode('utf-8')), filename=filename),
                 discord.File(io.BytesIO(resolved.original.encode('utf-8')), filename='original.lua')]
        await interaction.followup.send(message, files=files, ephemeral=True)
        return
    if analysis and analysis.kind == 'aryq':
        report = analysis.report
        message = (f'Partial analysis: {report.strings} candidate strings and {report.numbers} numeric '
                   f'constants from {report.constant_blocks} pools, assuming the normal runtime. '
                   f'Partially decoded {analysis.count} literal strings in the Lua file. '
                   'The VM has not been reconstructed into readable Lua. The unpacked VM is '
                   'experimental and may behave differently; keep the original.')
        files = [discord.File(io.BytesIO(report.text.encode('utf-8')), filename='aryq-analysis.txt'),
                 discord.File(io.BytesIO(analysis.source.encode('utf-8')), filename='partially-decoded.lua'),
                 discord.File(io.BytesIO(report.unpacked.encode('utf-8')), filename='experimental-unpacked-vm.lua'),
                 discord.File(io.BytesIO(resolved.original.encode('utf-8')), filename='original.lua')]
        await interaction.followup.send(message, files=files, ephemeral=True)
        return
    if analysis and analysis.kind == 'partial':
        message = (f'Partially decoded {analysis.count} literal strings using a recognized '
                   f'{analysis.label} pattern. The remaining VM or obfuscation has not been '
                   'reconstructed into readable Lua.')
        files = [discord.File(io.BytesIO(analysis.source.encode('utf-8')), filename='partially-decoded.lua'),
                 discord.File(io.BytesIO(resolved.original.encode('utf-8')), filename='original.lua')]
        await interaction.followup.send(message, files=files, ephemeral=True)
        return
    if analysis and analysis.kind == 'dispatcher':
        message = (f'Experimental partial VM cleanup: simplified {analysis.count} dispatcher blocks. '
                   'The VM is still present; the original source has not been reconstructed. '
                   'The output may behave differently or run slower. Keep and compare the original.')
        cleaned = await asyncio.to_thread(analyze_source, analysis.source)
        output = cleaned['source'] if isinstance(cleaned, dict) else analysis.source
        files = [discord.File(io.BytesIO(output.encode('utf-8')), filename='partial-vm.lua'),
                 discord.File(io.BytesIO(resolved.original.encode('utf-8')), filename='original.lua')]
        if isinstance(cleaned, dict) and cleaned['changes']:
            message += f" Simplified {cleaned['changes']} literal expressions; strings are included in strings.txt."
            files.append(discord.File(io.BytesIO(strings_report(cleaned).encode()), filename='strings.txt'))
        await interaction.followup.send(message, files=files, ephemeral=True)
        return
    if analysis is None or analysis.kind == 'unsupported':
        cleaned = await asyncio.to_thread(analyze_source, result.source)
        if isinstance(cleaned, dict) and cleaned['changes']:
            message = (f"Partial static cleanup: {cleaned['changes']} literal expressions simplified. "
                       'The VM, if present, has not been reconstructed. Candidate strings are guesses; keep the original.')
            files = [discord.File(io.BytesIO(cleaned['source'].encode()), filename='partially-decoded.lua'),
                     discord.File(io.BytesIO(resolved.original.encode()), filename='original.lua'),
                     discord.File(io.BytesIO(strings_report(cleaned).encode()), filename='strings.txt')]
            await interaction.followup.send(message, files=files, ephemeral=True)
            return
    if analysis and analysis.kind == 'busy':
        await interaction.followup.send('VM analysis is busy. Please try again in a moment.', ephemeral=True)
        return
    if analysis and analysis.kind == 'report':
        report = analysis.report
        message = (f'Partial {analysis.label} analysis: {report.functions} functions, '
                   f'{report.instructions} instruction slots, {report.constants} constants, and '
                   f'{report.recovered} possible decoded strings. The original Lua has not been reconstructed; '
                   'some strings may remain encrypted.')
        if report.truncated:
            message += ' The report was truncated to fit the file limit.'
        files = [discord.File(io.BytesIO(report.text.encode('utf-8')), filename='vm-analysis.txt'),
                 discord.File(io.BytesIO(resolved.original.encode('utf-8')), filename='original.lua')]
        await interaction.followup.send(message, files=files, ephemeral=True)
        return
    if analysis and analysis.kind == 'unsupported' and not fetched:
        await interaction.followup.send(
            f'The header says {analysis.label}; a header alone does not verify the format. '
            'No decoder for this VM is available, so the original Lua has not been reconstructed.',
            ephemeral=True)
        return
    if not result.layers and not fetched:
        await interaction.followup.send('No supported transformation was found. The source may already be readable or use an unknown VM. Try `/analyze` or `/strings`.', ephemeral=True)
        return
    message = (f'Downloaded public source and decoded {decoded_layers} wrapper layer(s).'
               if fetched else f'Decoded {decoded_layers} wrapper layer(s).')
    if analysis and analysis.kind == 'unsupported':
        message += (f' The header says {analysis.label}; this is not proof of its format. '
                    'No decoder for that VM is available, so the source remains obfuscated.')
    elif fetched and result.source == resolved.original:
        message += ' No supported wrapper was decoded from the downloaded file; it is returned unchanged and may still be obfuscated.'
    if reached_limit:
        message += f' The {MAX_LAYERS}-layer limit was reached; submit the output again to continue.'
    filename = 'original.lua' if fetched and result.source == resolved.original else 'deblofed.lua'
    await interaction.followup.send(message, file=discord.File(io.BytesIO(result.source.encode('utf-8')), filename=filename), ephemeral=True)


def strings_report(data):
    if not isinstance(data, dict):
        return 'Static string discovery was unavailable (unsupported syntax, size or resource limit).'
    return ('Literal strings (byte escapes rendered where needed):\n' + '\n'.join(data['strings']) +
            '\n\nBuiltin-call candidates (assume standard functions; not substituted):\n' +
            '\n'.join(data['candidates']) +
            '\n\nRepeating-XOR candidates (helper pattern present; calls may be unrelated):\n' +
            '\n'.join(data.get('xor_candidates', [])))


async def diagnostic(interaction, file, source, mode):
    if (file is None) == (source is None):
        await interaction.response.send_message('Provide exactly one of `file` or `source`.', ephemeral=True)
        return
    if _JOBS.locked():
        await interaction.response.send_message('The bot is processing another request. Please try again shortly.', ephemeral=True)
        return
    async with _JOBS:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if file is not None:
                if file.size > MAX_INPUT_BYTES:
                    raise ValueError('The file exceeds the 2 MB limit.')
                raw = await file.read()
                if len(raw) > MAX_INPUT_BYTES:
                    raise ValueError('The file exceeds the 2 MB limit.')
                source = raw.decode('utf-8-sig')
            resolved = await asyncio.wait_for(resolve_source(source, fetcher=fetch_source), timeout=45)
        except (ValueError, discord.HTTPException, asyncio.TimeoutError) as exc:
            message = 'The file must be UTF-8 encoded.' if isinstance(exc, UnicodeDecodeError) else str(exc)
            await interaction.followup.send(message or 'The download timed out. Try a direct file link.', ephemeral=True)
            return
        data = await asyncio.to_thread(analyze_source, resolved.source)
        if mode == 'format':
            if not isinstance(data, dict):
                await interaction.followup.send('Formatting unavailable: unsupported syntax, 200 KB limit, or analysis busy. Try again with a smaller file.', ephemeral=True)
                return
            files = [discord.File(io.BytesIO(data['source'].encode()), filename='formatted.lua'),
                     discord.File(io.BytesIO(resolved.original.encode()), filename='original.lua')]
            await interaction.followup.send('Formatted supported Lua and simplified pure literals. This does not remove an unknown VM.', files=files, ephemeral=True)
            return
        report = strings_report(data)
        vm = await asyncio.to_thread(analyze_vm, resolved.source)
        if vm is not None and vm.report is not None:
            report += '\n\nVerified-handler report (subject to its documented assumptions):\n' + vm.report.text
        if mode == 'analyze':
            finding = vm.kind + ': ' + vm.label if vm else 'No supported VM handler matched.'
            report = (f'Source bytes: {len(resolved.source.encode())}\n'
                      f'SHA-256: {hashlib.sha256(resolved.source.encode()).hexdigest()}\n'
                      f'Downloads: {resolved.downloads}\nDecoded wrapper layers: {resolved.layers}\n'
                      f'VM analysis: {finding}\n'
                      'Header matches and partial cleanup do not prove full devirtualization.\n\n' + report)
        await interaction.followup.send('Static analysis complete. Candidate decoded strings are unverified; no submitted Lua was run.',
                                        file=discord.File(io.BytesIO(report.encode()), filename=mode + '.txt'), ephemeral=True)


@bot.tree.command(name='strings', description='Extract literal strings and candidate static decodings')
@app_commands.checks.cooldown(1, 5.0, key=lambda interaction: interaction.user.id)
async def strings_command(interaction: discord.Interaction, file: discord.Attachment | None = None, source: str | None = None):
    await diagnostic(interaction, file, source, 'strings')


@bot.tree.command(name='analyze', description='Inspect source, loader chain and supported VM handling')
@app_commands.checks.cooldown(1, 5.0, key=lambda interaction: interaction.user.id)
async def analyze_command(interaction: discord.Interaction, file: discord.Attachment | None = None, source: str | None = None):
    await diagnostic(interaction, file, source, 'analyze')


@bot.tree.command(name='format', description='Format supported Lua syntax and simplify pure literals')
@app_commands.checks.cooldown(1, 5.0, key=lambda interaction: interaction.user.id)
async def format_command(interaction: discord.Interaction, file: discord.Attachment | None = None, source: str | None = None):
    await diagnostic(interaction, file, source, 'format')


@deblof.error
async def deblof_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message('Please wait a few seconds before trying again.', ephemeral=True)
    else:
        logging.error('Discord command failed: %s', type(error).__name__)
        if interaction.response.is_done():
            await interaction.followup.send('The command failed. Please try again.', ephemeral=True)
        else:
            await interaction.response.send_message('The command failed. Please try again.', ephemeral=True)


for command in (strings_command, analyze_command, format_command):
    command.error(deblof_error)


def main():
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise SystemExit('Set the DISCORD_TOKEN environment variable before starting the bot.')
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        raise SystemExit('Discord rejected the token. Check DISCORD_TOKEN and try again.') from None


if __name__ == '__main__':
    main()
