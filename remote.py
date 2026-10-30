"""Fetch bounded public source from explicitly supported raw source hosts."""

import asyncio
import re
from urllib.parse import urlsplit

import aiohttp

from deobfuscator import MAX_INPUT_BYTES

ALLOWED_HOSTS = {'raw.githubusercontent.com', 'gist.githubusercontent.com'}
_DOWNLOADS = asyncio.Semaphore(3)


class DownloadError(ValueError):
    """The requested public source could not be safely fetched."""


def canonical_url(url: str) -> str:
    if (len(url) > 2048 or any(ord(char) < 33 or ord(char) == 127 for char in url)
            or '\\' in url):
        raise DownloadError('That link is not supported.')
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise DownloadError('That link is not supported.') from exc
    host = parsed.netloc.lower()
    if parsed.scheme != 'https' or parsed.fragment or parsed.username or parsed.password:
        raise DownloadError('Use a supported public HTTPS link without credentials or fragments.')
    parts = parsed.path.split('/')
    safe_parts = all(part and part not in ('.', '..') and '%' not in part for part in parts[1:])
    if host in ALLOWED_HOSTS and bool(parsed.path) and not parsed.query:
        return url
    if host == 'github.com' and len(parts) >= 6 and parts[3] in ('blob', 'raw') and safe_parts and parsed.query in ('', 'raw=true', 'raw=1'):
        return 'https://raw.githubusercontent.com/' + '/'.join(parts[1:3] + parts[4:])
    if host == 'gitlab.com' and safe_parts and parsed.query in ('', 'ref_type=heads'):
        # Subgroups are allowed; the /-/ marker unambiguously ends the project path.
        markers = [i for i, part in enumerate(parts) if part == '-']
        if len(markers) == 1:
            i = markers[0]
            if i >= 3 and len(parts) >= i + 4 and parts[i + 1] in ('raw', 'blob'):
                parts[i + 1] = 'raw'
                return 'https://gitlab.com' + '/'.join(parts) + ('?' + parsed.query if parsed.query else '')
    if host in ('pastebin.com', 'www.pastebin.com') and not parsed.query:
        if re.fullmatch(r'/(?:raw/)?[A-Za-z0-9]{1,32}/?', parsed.path):
            return 'https://pastebin.com/raw/' + parsed.path.rstrip('/').rsplit('/', 1)[-1]
    if host == 'pastefy.app' and not parsed.query:
        match = re.fullmatch(r'/([A-Za-z0-9]{1,32})(?:/raw)?/?', parsed.path)
        if match:
            return 'https://pastefy.app/' + match.group(1) + '/raw'
    if host == 'cdn.jsdelivr.net' and not parsed.query:
        # Restrict the CDN to public GitHub source, never npm packages or arbitrary endpoints.
        if re.fullmatch(r'/gh/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+/.+', parsed.path) and safe_parts:
            return url
    raise DownloadError('Supported sources: GitHub raw/blob, GitLab raw/blob, Pastebin, Pastefy and jsDelivr GitHub files.')


def validate_url(url: str) -> None:
    canonical_url(url)


async def fetch_source(url: str) -> str:
    url = canonical_url(url)
    try:
        async with _DOWNLOADS:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout, auto_decompress=False,
                                             headers={'Accept-Encoding': 'identity'}) as session:
                async with session.get(url, allow_redirects=False) as response:
                    if response.status != 200:
                        if response.status == 404:
                            raise DownloadError('The source was not found (HTTP 404). Check the file path, branch and whether it is public.')
                        if response.status in (401, 403):
                            raise DownloadError('The host denied access. The source may be private or blocked by the provider.')
                        if response.status == 429:
                            raise DownloadError('The source host is rate limiting downloads. Please try again later.')
                        raise DownloadError('The public link could not be downloaded (redirects are not followed).')
                    if response.headers.get('Content-Type', '').split(';', 1)[0].lower() in ('text/html', 'application/xhtml+xml'):
                        raise DownloadError('The host returned a web page instead of raw Lua. Check that the paste is public.')
                    if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                        raise DownloadError('Compressed responses are not supported.')
                    if response.content_length is not None and response.content_length > MAX_INPUT_BYTES:
                        raise DownloadError('The downloaded file exceeds the 2 MB limit.')
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > MAX_INPUT_BYTES:
                            raise DownloadError('The downloaded file exceeds the 2 MB limit.')
                    try:
                        text = data.decode('utf-8-sig')
                    except UnicodeDecodeError as exc:
                        raise DownloadError('The downloaded file must be UTF-8.') from exc
                    if text[:512].lstrip().lower().startswith(('<!doctype html', '<html')):
                        raise DownloadError('The host returned HTML instead of raw Lua.')
                    return text
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise DownloadError('The public link could not be downloaded. Please try again later.') from exc
