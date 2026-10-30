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
    github = host in ALLOWED_HOSTS and bool(parsed.path) and not parsed.query
    parts = parsed.path.split('/')
    gitlab = (host == 'gitlab.com' and parsed.query in ('', 'ref_type=heads')
              and len(parts) >= 7 and parts[0] == '' and parts[3:5] == ['-', 'raw']
              and all(part and part not in ('.', '..') and '%' not in part for part in parts[1:]))
    pastebin = (host in ('pastebin.com', 'www.pastebin.com') and not parsed.query
                and re.fullmatch(r'/(?:raw/)?[A-Za-z0-9]{1,32}/?', parsed.path))
    if parsed.scheme != 'https' or parsed.fragment or not (github or gitlab or pastebin):
        raise DownloadError('Only supported public GitHub, GitLab or Pastebin HTTPS links are allowed.')
    if pastebin:
        return 'https://pastebin.com/raw/' + parsed.path.rstrip('/').rsplit('/', 1)[-1]
    return url


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
                        raise DownloadError('The public link could not be downloaded (redirects are not followed).')
                    if response.headers.get('Content-Type', '').split(';', 1)[0].lower() == 'text/html':
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
                        return data.decode('utf-8-sig')
                    except UnicodeDecodeError as exc:
                        raise DownloadError('The downloaded file must be UTF-8.') from exc
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise DownloadError('The public link could not be downloaded. Please try again later.') from exc
