import unittest
from unittest.mock import AsyncMock
from pipeline import resolve_source, MAX_LINKS
from remote import DownloadError

BASE = 'https://raw.githubusercontent.com/a/b/main/'

class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_chained_loaders_and_literal_wrappers(self):
        fetch = AsyncMock(side_effect=[f'loadstring(game:HttpGet("{BASE}b.lua"))()', 'loadstring("print(42)")()'])
        result = await resolve_source(BASE + 'a.lua', fetcher=fetch)
        self.assertEqual((result.source, result.downloads, result.layers), ('print(42)', 2, 1))
        self.assertEqual(result.original, 'loadstring("print(42)")()')

    async def test_normalized_cycle_is_blocked_before_second_download(self):
        fetch = AsyncMock(return_value='https://github.com/a/b/blob/main/a.lua')
        with self.assertRaisesRegex(DownloadError, 'cycle'):
            await resolve_source(BASE + 'a.lua', fetcher=fetch)
        self.assertEqual(fetch.await_count, 1)

    async def test_five_link_limit(self):
        fetch = AsyncMock(side_effect=[BASE + str(i) + '.lua' for i in range(1, 7)])
        with self.assertRaisesRegex(DownloadError, 'five-link'):
            await resolve_source(BASE + '0.lua', fetcher=fetch)
        self.assertEqual(fetch.await_count, MAX_LINKS)

    async def test_download_size_limits(self):
        with self.assertRaisesRegex(DownloadError, '2 MB'):
            await resolve_source(BASE + 'a.lua', fetcher=AsyncMock(return_value='x' * 2_000_001))
        # Large comment-only loaders stay under per-source cap but exceed aggregate cap.
        # Use whitespace rather than locals, to keep whole-script URL recognition valid.
        responses = [' ' * 1_350_000 + BASE + str(i) + '.lua' for i in range(1, 4)]
        with self.assertRaisesRegex(DownloadError, '4 MB'):
            await resolve_source(BASE + 'a.lua', fetcher=AsyncMock(side_effect=responses))

    async def test_blocked_nested_host_never_fetched(self):
        fetch = AsyncMock(return_value='loadstring(game:HttpGet("http://127.0.0.1/private"))()')
        with self.assertRaises(DownloadError):
            await resolve_source(BASE + 'a.lua', fetcher=fetch)
        self.assertEqual(fetch.await_count, 1)
