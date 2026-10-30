# Lua deobfuscation Discord bot

A Discord bot that decodes supported Lua/Luau wrappers, translates two recognized VM formats, performs partial dispatcher cleanup, and extracts strings. Submitted Lua is never executed. All command responses are private.

This release is stronger than the original eight-layer wrapper decoder. It is **not a universal VM deobfuscator**. Readable output can still contain register tables and state blocks; original names and comments lost during obfuscation cannot be restored.

See [CHAT_SUMMARY.md](CHAT_SUMMARY.md) for the project history and decisions from our conversation.

## Quick setup on bot-hosting.net

1. Stop the existing bot and upload/extract the ZIP into the application's main directory. Replace the old Python files, including the `vendor/` directory. Keep your hosting environment variables.
2. Use the **Python** runtime. Set the startup file to **`bot.py`**. `main.py` is also provided for hosts that default to that filename. Filenames are case-sensitive: `Bot.py` is different.
3. Install dependencies with `python -m pip install -r requirements.txt` if your host does not install them automatically.
4. In **Environment**, add key `DISCORD_TOKEN`, paste your current bot token as its value, and enable **Secret**. Do not paste the token into code or chat. Rotate any previously exposed token in Discord's Developer Portal.
5. Optionally add `DISCORD_GUILD_ID` with your Discord server ID for fast command registration in that server. Otherwise commands are global and can take time to appear.
6. Start the bot. Use `/help` in Discord. If new commands do not appear, restart and check the bot invite has both `bot` and `applications.commands` scopes.

The bot needs permission to send messages and attach files. No privileged message-content intent is needed. Do not start a second copy with the same token.

## Local setup

Requires Python **3.10+** and the packages in `requirements.txt`:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# Configure DISCORD_TOKEN privately in your environment.
python bot.py
```

Linux is required for the bounded subprocess analysis features because they enforce CPU and memory limits with `resource`. Lua/Luau is **not** required to run the bot.

## Commands

Every input command accepts exactly one of `file` or `source`. Use a UTF-8 `.lua` file, pasted Lua, a supported URL, or a whole-script `loadstring(game:HttpGet("..."))()` / `HttpGetAsync` loader.

| Command | What it does |
| --- | --- |
| `/deblof` | Decode wrappers; translate a recognized VM; otherwise attempt partial static cleanup. |
| `/strings` | List literal strings, standard-builtin decoding candidates, and repeating-XOR candidates when the supported helper structure is present. |
| `/analyze` | Report final source size/SHA-256, wrapper/download counts, matching VM handling, and strings. |
| `/format` | Format supported Lua syntax, decode string escapes, and simplify pure constant expressions; preserve the original file. |
| `/help` | Show capabilities and limits. |

Strings marked as **candidates** assume ordinary builtin behavior, or may come from unrelated two-string calls. They are never silently substituted for arbitrary calls. `/format` does not remove an unknown VM or validate Roblox behavior. Typed Luau, unusual syntax and newer language constructs may be unsupported by the static parser.

## What improved

- **32 supported wrapper layers per source**, increased from 8. Inputs still have size limits.
- **Up to five linked public loaders**, with cycle detection, per-file and aggregate byte limits, and a command download deadline.
- Comments around supported wrappers, byte-sized integer addition/subtraction inside `string.char`, and literal `string.sub`, including negative indexes. Literal `string.char(unpack({...}))` / `table.unpack` byte tables and local URL variables are also supported.
- Literal string escapes and string concatenations can be simplified throughout a script, not just inside a whole-script loader. Pure numeric `+`, `-`, `*`, `/` expressions can be folded within conservative limits. Calls and variables are not evaluated.
- Formatting uses source ranges and whitespace edits, preserving syntax, string bytes, short-circuit expressions and multiple returns. The original is attached for partial results.
- Prometheus-style dispatcher cleanup now supports multiline code, Unicode and comments. Its result also receives literal cleanup where possible.
- The known Gist interpreter tolerates whitespace/comments in its header when its exact token fingerprint still matches. Changed interpreter instructions remain unsupported.
- Better messages for missing files, denied access, rate limits, HTML pages and loader cycles. Unchanged downloads are called `original.lua`.
- One heavy request runs at a time to reduce memory pressure on small hosts; other users receive a busy message. Worker parsing has CPU/memory limits.

### Supported literal wrappers

Whole-script `load`/`loadstring` wrappers can use optional simple local string assignments, quoted and long-bracket strings, Lua byte escapes, `..`, `string.char`, byte XOR, `string.reverse`, `string.rep`, `string.sub`, and literal `table.concat` lists.

Base64 calls recognized by name: `base64_decode`, `base64decode`, `base64.decode`, `crypt.base64decode`, `syn.crypt.base64.decode`. Hex calls: `hex_decode`, `hexdecode`, `hex.decode`, `crypt.hexdecode`. These names assume standard encodings, not arbitrary custom function implementations.

A limit counts only **supported wrappers**. An unknown VM can still prevent recovery after a single layer.

## Supported links

| Provider / form | Handling |
| --- | --- |
| `raw.githubusercontent.com/...`, `gist.githubusercontent.com/...` | Public raw text; no query parameters. |
| `github.com/owner/repo/blob/ref/path` or `/raw/ref/path` | Converted directly to `raw.githubusercontent.com`; optional `raw=true`/`raw=1`. |
| `gitlab.com/group/project/-/raw/ref/path` or `/blob/ref/path` | Blob converted to raw; subgroups allowed; optional `ref_type=heads`. |
| `pastebin.com/id`, `www.pastebin.com/id`, `/raw/id` | Converted to the raw endpoint. |
| `pastefy.app/id`, `/id/raw` | Converted to `/id/raw`; use public single-file text pastes. Multi-file JSON bundles are not unpacked as Lua. |
| `cdn.jsdelivr.net/gh/owner/repo@ref/path` | Public GitHub files only; npm/CDN combine endpoints are unsupported. |

Only explicitly supported HTTPS hosts are contacted. Credentials, explicit ports, fragments, arbitrary query parameters and HTTP redirects are rejected. Private content, provider challenges and removed links may fail. There is no general arbitrary-URL fetcher. Rentry is not included because its current documented raw-access rules require provider-issued access codes.

## VM support and honest limits

- **Known Undead Defense Tycoon Gist interpreter:** translate its verified instructions to explicit Lua operations, functions, branches and closures. Some graphs retain state blocks. The interpreter signature must match.
- **Exact supplied Aryq GitLab snapshot:** translate 482 functions under its documented ordinary-runtime key assumption. Different Aryq samples are not automatically supported.
- **Recognized Prometheus-style dispatchers:** flatten a balanced dispatcher, then simplify literals and format the result. The VM remains. For the supplied Skysaken snapshot, 252 blocks are recognized and literal/string cleanup gives additional readability.
- **Byte-addition table pattern:** partial literal recovery.
- **Luraph header:** recognize the label only; no Luraph decoder is included.
- **Other/custom VMs:** literal cleanup and diagnostics may help, but full reconstruction is not promised.

No original user script is executed for analysis. Development behavior tests use independently authored harmless fixtures; full supplied outputs are checked with Luau's compiler. Compilation is not proof of equivalent Roblox behavior. Retain originals and test recovered code in the intended environment.

## Limits

| Resource | Bound |
| --- | --- |
| Input or individual downloaded file | 2 MB, UTF-8 |
| Decoded wrapper result | 1 MB |
| Wrapper layers | 32 per source |
| Public loader links | 5 per request; 4 MB aggregate downloaded text |
| Download time | 15 seconds per download; 45 seconds for resolution |
| Literal cleanup / formatting / string discovery | 200 KB input |
| Dispatcher cleanup | 100 KB input; up to 512 leaves |
| Translated VM output | 8 MB |
| Per-user command cooldown | 5 seconds for each input command |
| Concurrent heavy requests | 1 |

Static workers have a 4-second CPU limit, 6-second parent timeout, and enforced address-space limits. Unsupported syntax, time/resource limits and busy workers can return an unavailable/partial result. These bounds do not guarantee a particular hosting plan's memory or CPU will be sufficient. The bot does not persist submitted files to disk.

## Testing

```sh
python -m unittest discover -s tests -v
```

Behavior checks use authored fixtures. Install Lua 5.1 for cleanup/dispatcher comparisons; install Luau or set `LUAU_BIN=/path/to/luau` for VM tests, including floor division. Neither interpreter is required in the deployed ZIP. Without them, runtime-dependent tests report skips.

## Third-party code

The static parser includes unprom code at commit `d37b9d97746160359fb68dbdbb5213317403e669`, under the MIT license in `vendor/unprom/LICENSE`.
