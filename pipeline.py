"""Resolve supported static wrappers and bounded chains of public source links."""
from dataclasses import dataclass
from deobfuscator import MAX_INPUT_BYTES, MAX_LAYERS, deobfuscate, remote_url
from remote import canonical_url, fetch_source, DownloadError
import asyncio

MAX_LINKS = 5
MAX_TOTAL_DOWNLOAD_BYTES = 4_000_000


@dataclass
class Resolved:
    source: str
    layers: int
    downloads: int
    limited: bool
    original: str


async def resolve_source(source, *, fetcher=None):
    fetcher = fetch_source if fetcher is None else fetcher
    if len(source.encode('utf-8')) > MAX_INPUT_BYTES:
        raise ValueError('The input exceeds the 2 MB limit.')
    original = source
    layers, downloads, total = 0, 0, 0
    visited = set()
    while True:
        result = await asyncio.to_thread(deobfuscate, source)
        layers += result.layers
        source = result.source
        url = remote_url(source)
        if not url:
            return Resolved(source, layers, downloads, result.layers == MAX_LAYERS, original)
        url = canonical_url(url)
        if url in visited:
            raise DownloadError('A loader link cycle was detected. The same source would be downloaded again.')
        if downloads >= MAX_LINKS:
            raise DownloadError('The five-link loader limit was reached. Submit the final loader as a new request.')
        visited.add(url)
        source = await fetcher(url)
        if len(source.encode('utf-8')) > MAX_INPUT_BYTES:
            raise DownloadError('The downloaded file exceeds the 2 MB limit.')
        total += len(source.encode('utf-8'))
        if total > MAX_TOTAL_DOWNLOAD_BYTES:
            raise DownloadError('This loader chain exceeds the 4 MB total download limit.')
        downloads += 1
        # Retain the final downloaded file before any local decoding.
        original = source
