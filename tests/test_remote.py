import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deobfuscator import MAX_INPUT_BYTES
from remote import DownloadError, canonical_url, fetch_source, validate_url


class Chunks:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class Response:
    def __init__(self, *, status=200, headers=None, content_length=None, chunks=(b'print(1)',)):
        self.status = status
        self.headers = headers or {}
        self.content_length = content_length
        self.content = Chunks(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    def __init__(self, response):
        self.response = response
        self.options = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **options):
        self.options = options
        return self.response


class UrlTests(unittest.IsolatedAsyncioTestCase):
    URL = 'https://raw.githubusercontent.com/a/b/main/test.lua'

    def test_url_allowlist(self):
        validate_url(self.URL)
        validate_url('https://gist.githubusercontent.com/a/b/raw/test.lua')
        validate_url('https://gitlab.com/babft/weshkyv3/-/raw/main/source/source?ref_type=heads')
        validate_url('https://gitlab.com/babft/weshkyv3/-/raw/main/source')
        for url in ('http://raw.githubusercontent.com/a', 'https://127.0.0.1/a',
                    'https://raw.githubusercontent.com.evil.test/a',
                    'https://raw.githubusercontent.com@evil.test/a',
                    'https://evil.test@raw.githubusercontent.com/a',
                    'https://raw.githubusercontent.com:443/a',
                    'https://raw.githubusercontent.com/a?token=secret',
                    'https://raw.githubusercontent.com/a#fragment',
                    'https://raw.githubusercontent.com/\\evil',
                    'https://gitlab.com/api/v4/projects/1',
                    'https://gitlab.com/g/p/-/raw/../secret',
                    'https://gitlab.com/g/p/-/raw/main/%2e%2e/a',
                    'https://gitlab.com/g/p/-/raw/main/a?ref_type=evil',
                    'https://gitlab.com:443/g/p/-/raw/main/a',
                    'https://raw.githubusercontent.com/\nsecret'):
            with self.subTest(url=url), self.assertRaises(DownloadError):
                validate_url(url)

    def test_pastebin_page_and_raw_links(self):
        for url in ('https://pastebin.com/Ab12Cd34', 'https://www.pastebin.com/Ab12Cd34/',
                    'https://pastebin.com/raw/Ab12Cd34'):
            self.assertEqual(canonical_url(url), 'https://pastebin.com/raw/Ab12Cd34')
        for path in ('https://pastebin.com/api/api_post.php', 'https://pastebin.com/../secret',
                     'https://pastebin.com/raw/%2e%2e', 'https://pastebin.com/x?token=secret',
                     'https://evil@pastebin.com/abc', 'https://pastebin.com:443/abc',
                     'https://pastebin.com.evil/abc'):
            with self.subTest(url=path), self.assertRaises(DownloadError):
                canonical_url(path)

    def test_new_source_link_normalization(self):
        cases = {
            'https://github.com/a/b/blob/main/test.lua': 'https://raw.githubusercontent.com/a/b/main/test.lua',
            'https://github.com/a/b/raw/main/test.lua?raw=1': 'https://raw.githubusercontent.com/a/b/main/test.lua',
            'https://gitlab.com/group/sub/project/-/blob/main/a.lua': 'https://gitlab.com/group/sub/project/-/raw/main/a.lua',
            'https://pastefy.app/Ab12Cd34': 'https://pastefy.app/Ab12Cd34/raw',
            'https://pastefy.app/Ab12Cd34/raw': 'https://pastefy.app/Ab12Cd34/raw',
            'https://cdn.jsdelivr.net/gh/a/b@main/a.lua': 'https://cdn.jsdelivr.net/gh/a/b@main/a.lua',
        }
        for url, target in cases.items():
            self.assertEqual(canonical_url(url), target)
        for url in ('https://github.com/a/b/blob/main/../secret',
                    'https://github.com/a/b/blob/main/%2e%2e/secret',
                    'https://github.com:443/a/b/blob/main/a.lua',
                    'https://github.com/a/b/blob/main/a.lua?token=secret',
                    'https://pastefy.app/abc/raw?x=y',
                    'https://pastefy.app.evil/abc',
                    'https://cdn.jsdelivr.net/npm/pkg/file',
                    'https://cdn.jsdelivr.net/gh/a/b@main/../file',
                    'https://evil@cdn.jsdelivr.net/gh/a/b@main/a.lua'):
            with self.subTest(url=url), self.assertRaises(DownloadError):
                canonical_url(url)

    async def test_html_sniff_and_specific_http_errors(self):
        cases = [(Response(status=404), '404'), (Response(status=403), 'denied'),
                 (Response(status=429), 'rate limiting'),
                 (Response(chunks=(b'<!DOCTYPE html><title>Challenge</title>',)), 'HTML'),
                 (Response(chunks=(b'\xef\xbb\xbf<!DOCTYPE html>',)), 'HTML')]
        for response, message in cases:
            with patch('remote.aiohttp.ClientSession', return_value=Session(response)):
                with self.assertRaisesRegex(DownloadError, message):
                    await fetch_source(self.URL)

    async def test_pastebin_fetch_uses_raw_endpoint(self):
        session = Session(Response())
        with patch.object(session, 'get', wraps=session.get) as get:
            with patch('remote.aiohttp.ClientSession', return_value=session):
                self.assertEqual(await fetch_source('https://pastebin.com/Ab12Cd34'), 'print(1)')
            get.assert_called_once_with('https://pastebin.com/raw/Ab12Cd34', allow_redirects=False)

    async def test_download_and_no_redirects(self):
        session = Session(Response(chunks=(b'print', b'(1)')))
        with patch('remote.aiohttp.ClientSession', return_value=session):
            self.assertEqual(await fetch_source(self.URL), 'print(1)')
        self.assertEqual(session.options, {'allow_redirects': False})

    async def test_reject_redirect_compression_oversize_and_non_utf8(self):
        responses = [Response(status=302), Response(headers={'Content-Type': 'text/html; charset=utf-8'}), Response(headers={'Content-Encoding': 'gzip'}),
                     Response(content_length=MAX_INPUT_BYTES + 1),
                     Response(chunks=(b'a' * MAX_INPUT_BYTES, b'b')), Response(chunks=(b'\xff',))]
        for response in responses:
            with self.subTest(response=response), patch('remote.aiohttp.ClientSession', return_value=Session(response)):
                with self.assertRaises(DownloadError):
                    await fetch_source(self.URL)

    async def test_blocked_host_never_opens_session(self):
        with patch('remote.aiohttp.ClientSession') as session:
            with self.assertRaises(DownloadError):
                await fetch_source('https://127.0.0.1/admin')
            session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
