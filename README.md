# Lua deobfuscation Discord bot

A Discord bot with `/deblof` and `/help`. It decodes supported Lua wrappers without running submitted code. Responses are private to the person who runs the command.

## Setup

1. Create a Discord application and bot in the Discord Developer Portal. Keep the bot token secret and rotate it if it is ever exposed.
2. Invite the bot to your server with the `bot` and `applications.commands` scopes. The bot needs permission to send messages and attach files.
3. Install Python 3.10 or newer, then run:

   ```sh
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```

4. Set `DISCORD_TOKEN` in your hosting provider's secret settings or local environment. Optionally set `DISCORD_GUILD_ID` to a server ID for fast command registration in that server. Without it, commands are registered globally and may take time to appear. Restart the bot after changing either setting.
5. Start the bot with `python bot.py`. Keep the process running to receive commands.

Use `/deblof` with **either** the `file` option or the `source` option. The decoded result is returned as a `.lua` attachment. Use `/help` to see the supported patterns.

## Supported patterns

The entire input must contain optional simple `local` assignments followed by a `load(...)` or `loadstring(...)` call, optionally preceded by `return` and followed by `()` and/or a semicolon. Expressions can contain:

- Quoted and Lua long-bracket strings, decimal and hexadecimal byte escapes, and `..` concatenation.
- Previously declared simple local string variables.
- `string.char` with literal byte values or byte-sized `bit32.bxor`/`bit.bxor` expressions; `string.reverse`, `string.rep` with a literal count, and `table.concat` with a literal list and optional separator.
- Strict padded standard Base64 in calls named `base64_decode`, `base64decode`, `base64.decode`, `crypt.base64decode`, or `syn.crypt.base64.decode`. Hexadecimal decoding is supported for `hex_decode`, `hexdecode`, `hex.decode`, and `crypt.hexdecode`. These names are assumed to use the standard encodings; no Lua function definition is executed or verified.

For example, `local x = base64_decode("cHJpbnQoMSk="); return loadstring(x)()` produces `print(1)`. Up to eight nested wrappers are decoded per request. Input is limited to 2 MB and decoded wrapper layers to 1 MB; both must be UTF-8.

## Public links

Provide a direct link or a whole-script Roblox `loadstring(game:HttpGet("..."))()` or `HttpGetAsync` wrapper. Supported HTTPS sources:

- `raw.githubusercontent.com` and `gist.githubusercontent.com`, without queries.
- `gitlab.com/<group>/<project>/-/raw/<ref>/<path>`, optionally with `ref_type=heads`.
- `pastebin.com/<id>` and `pastebin.com/raw/<id>`. Page links and `www.pastebin.com` links are normalized to `https://pastebin.com/raw/<id>` before downloading.

Only public source is supported. Private, removed, login or challenge pages can fail. Credentials, explicit ports, fragments and redirects are rejected. Downloads require UTF-8, are limited to 2 MB, and have a 15-second timeout. HTML pages and compressed responses are rejected. One link is fetched per request; nested remote loaders are not recursively downloaded. A five-second per-user cooldown and a three-download concurrency limit apply.

## Recognized VM translation

For the recognized interpreter in the supplied Undead Defense Tycoon Gist and the **exact supplied Aryq GitLab snapshot**, the bot can now return `readable.lua`, a report, and the original source. The translated file replaces serialized instruction tables and opcode dispatch with explicit Lua operations and functions. It handles calls, returns, tables, closures and upvalues. Control flow is recovered as branches and loops when the graph can be structured; other functions retain explicit state blocks.

This is mechanically recovered Lua, with register values stored in a `values` table. Original variable names and comments cannot be recovered. Candidate XOR-decoded strings from the Gist are annotated without silently replacing their runtime calls. The output targets **Roblox Luau**; it can contain `continue` or integer division. It is not a promise of clean original source or behavior equivalence. Test the output in the intended environment and retain the original.

The Aryq decoder matches one complete script fingerprint and assumes its anti-tamper value is zero in an ordinary runtime. Other versions and changed key conditions are unsupported. Its translated functions include its original entry helper functions, formatted for reading. No user-submitted Lua is executed by the bot. Local Luau tools are used during development only to compile submitted output and to execute harmless, independently authored test programs.

Translation has bounds on input, instruction and function counts, graph structuring, and output (8 MB). Concurrent translations are bounded per format. If translation cannot finish safely, the existing partial report is returned where possible. Some recognizable byte-addition tables can be folded, and an experimental static pass can simplify certain Prometheus-style dispatchers up to 100 KB. These partial outputs retain obfuscation and may behave differently. Luraph headers are hints only; no Luraph decoder is included.

Arbitrary VM formats, arbitrary Lua evaluation and private source downloads remain unsupported. Decoded or translated output can still contain dangerous code; review it before running it.

## Tests

```sh
python -m unittest discover -s tests -v
```

Behavior tests execute only authored fixtures. If `luau` or `lua5.1` is installed, it is detected automatically; set `LUAU_BIN` to select a Luau executable. Luau is required for the floor-division test. The bot itself does not need either interpreter.

## Third-party code

The static parser includes code from unprom (commit `d37b9d97746160359fb68dbdbb5213317403e669`), provided under the MIT license in `vendor/unprom/LICENSE`.
