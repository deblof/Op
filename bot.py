"""Discord slash commands for statically decoding supported Lua wrappers."""

import asyncio
import io
import logging
import os

import discord
from discord import app_commands

from deobfuscator import MAX_INPUT_BYTES, deobfuscate, remote_url
from remote import DownloadError, fetch_source
from vm_formats import analyze_vm

HELP = (
    "Use `/deblof` with a Lua file, pasted code, or a public GitHub, GitLab or Pastebin link. "
    "Pastebin page links are converted to raw links. Roblox `loadstring(game:HttpGet(...))()` "
    "wrappers work with these supported hosts. Files and downloads must be UTF-8 and at most 2 MB. "
    "Literal decoding supports load/loadstring, simple locals, string escapes, string.char, "
    "byte XOR, reverse, rep, table.concat, and standard Base64/hex decoder calls, up to 8 layers. "
    "The recognized Gist VM and exact supplied Aryq sample can be translated to Lua with explicit "
    "operations, branches and closures. Complex control flow can retain state blocks. Original "
    "variable names are unavailable. Recovered source targets Luau; test it in your environment. "
    "Aryq translation assumes its standard-runtime keys. Unsupported variants return a partial "
    "report where possible. Byte-addition tables and some Prometheus-style dispatchers also have "
    "partial static handling. Luraph headers are recognized without a decoder. "
    "All analysis is static: submitted Lua is never run. Responses are private."
)


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
@app_commands.describe(file='A UTF-8 Lua file, up to 2 MB', source='Lua source or a public GitHub, GitLab or Pastebin link')
async def deblof(interaction: discord.Interaction, file: discord.Attachment | None = None,
                 source: str | None = None):
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
        result = await asyncio.to_thread(deobfuscate, source)
        decoded_layers = result.layers
        reached_limit = result.layers == 8
        url = remote_url(result.source)
        fetched = False
        if url:
            downloaded = await fetch_source(url)
            result = await asyncio.to_thread(deobfuscate, downloaded)
            decoded_layers += result.layers
            reached_limit = reached_limit or result.layers == 8
            fetched = True
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
                 discord.File(io.BytesIO(result.source.encode('utf-8')), filename='original.lua')]
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
                 discord.File(io.BytesIO(result.source.encode('utf-8')), filename='original.lua')]
        await interaction.followup.send(message, files=files, ephemeral=True)
        return
    if analysis and analysis.kind == 'partial':
        message = (f'Partially decoded {analysis.count} literal strings using a recognized '
                   f'{analysis.label} pattern. The remaining VM or obfuscation has not been '
                   'reconstructed into readable Lua.')
        await interaction.followup.send(message, file=discord.File(
            io.BytesIO(analysis.source.encode('utf-8')), filename='partially-decoded.lua'), ephemeral=True)
        return
    if analysis and analysis.kind == 'dispatcher':
        message = (f'Experimental partial VM cleanup: simplified {analysis.count} dispatcher blocks. '
                   'The VM is still present; the original source has not been reconstructed. '
                   'The output may behave differently or run slower. Keep and compare the original.')
        files = [discord.File(io.BytesIO(analysis.source.encode('utf-8')), filename='partial-vm.lua'),
                 discord.File(io.BytesIO(result.source.encode('utf-8')), filename='original.lua')]
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
                 discord.File(io.BytesIO(result.source.encode('utf-8')), filename='original.lua')]
        await interaction.followup.send(message, files=files, ephemeral=True)
        return
    if analysis and analysis.kind == 'unsupported' and not fetched:
        await interaction.followup.send(
            f'The header says {analysis.label}; a header alone does not verify the format. '
            'No decoder for this VM is available, so the original Lua has not been reconstructed.',
            ephemeral=True)
        return
    if not result.layers and not fetched:
        await interaction.followup.send('No supported whole-script wrapper was found. Use `/help` for supported patterns.', ephemeral=True)
        return
    message = (f'Downloaded public source and decoded {decoded_layers} wrapper layer(s).'
               if fetched else f'Decoded {decoded_layers} wrapper layer(s).')
    if analysis and analysis.kind == 'unsupported':
        message += (f' The header says {analysis.label}; this is not proof of its format. '
                    'No decoder for that VM is available, so the source remains obfuscated.')
    elif fetched and not result.layers:
        message += ' No supported wrapper was decoded from the downloaded file; it is returned unchanged and may still be obfuscated.'
    if reached_limit:
        message += ' The 8-layer limit was reached; submit the output again to continue.'
    filename = 'original.lua' if fetched and not result.layers else 'deblofed.lua'
    await interaction.followup.send(message, file=discord.File(io.BytesIO(result.source.encode('utf-8')), filename=filename), ephemeral=True)


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
