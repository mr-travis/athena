"""全部模拟 DNS 与网络，不请求真实网页。"""
import socket
import unittest
from unittest.mock import MagicMock, patch
import safe_http
import web_tools

URL = 'https://api-docs.deepseek.com/guide?q=1#part'
PUBLIC = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('8.8.8.8', 443))


class WebTests(unittest.TestCase):
    def test_invalid_urls_do_not_resolve(self):
        with patch('safe_http.socket.getaddrinfo') as dns:
            for url in [None, '', 'http://api-docs.deepseek.com/', 'https://127.0.0.1/', 'https://api-docs.deepseek.com.evil.org/', 'https://user@api-docs.deepseek.com/', 'https://api-docs.deepseek.com:444/', 'https://api-docs.deepseek.com/\n', 'https://api-docs.deepseek.com/中文']:
                with self.subTest(url=url):
                    self.assertFalse(web_tools.fetch_url(url)['ok'])
            dns.assert_not_called()

    def test_mixed_private_dns_never_connects(self):
        for ip in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '224.0.0.1']:
            with self.subTest(ip=ip), patch('safe_http.socket.getaddrinfo', return_value=[PUBLIC, (*PUBLIC[:4], (ip, 443))]), patch('safe_http.PinnedHTTPSConnection') as connection:
                self.assertEqual(web_tools.fetch_url(URL)['error_type'], 'AddressNotAllowed')
                connection.assert_not_called()

    def test_pinned_socket_and_tls_hostname(self):
        context = MagicMock()
        with patch('safe_http.ssl.create_default_context', return_value=context), patch('safe_http.socket.socket') as factory:
            connection = safe_http.PinnedHTTPSConnection('api-docs.deepseek.com', PUBLIC)
            connection.connect()
            factory.return_value.connect.assert_called_once_with(PUBLIC[4])
            context.wrap_socket.assert_called_once_with(factory.return_value, server_hostname='api-docs.deepseek.com')
            connection.close()

    def test_tls_failure_closes_socket(self):
        context = MagicMock()
        context.wrap_socket.side_effect = OSError('secret')
        with patch('safe_http.ssl.create_default_context', return_value=context), patch('safe_http.socket.socket') as factory:
            with self.assertRaises(OSError):
                safe_http.PinnedHTTPSConnection('api-docs.deepseek.com', PUBLIC).connect()
            factory.return_value.close.assert_called_once()

    def run_response(self, status=200, headers=None, data=b'hello', error=None):
        response = MagicMock(status=status)
        values = {'Content-Type': 'text/plain', **(headers or {})}
        response.getheader.side_effect = lambda key, default=None: values.get(key, default)
        response.read.return_value = data
        with patch('safe_http.socket.getaddrinfo', return_value=[PUBLIC]), patch('safe_http.PinnedHTTPSConnection') as factory:
            factory.return_value.getresponse.return_value = response
            factory.return_value.request.side_effect = error
            result = web_tools.fetch_url(URL)
            factory.assert_called_once_with('api-docs.deepseek.com', PUBLIC)
            factory.return_value.close.assert_called_once()
            if result['ok']:
                response.read.assert_called_once_with(safe_http.MAX_DOWNLOAD_BYTES + 1)
                self.assertEqual(factory.return_value.request.call_args.args, ('GET', '/guide?q=1'))
            return result

    def test_success_and_bounded_read(self):
        self.assertEqual(self.run_response(), {'ok': True, 'url': URL, 'content': 'hello', 'truncated': False})

    def test_response_rejections(self):
        cases = [({'status':302}, 'RedirectNotAllowed'), ({'status':404}, 'HTTPError'),
                 ({'headers':{'Content-Encoding':'gzip'}}, 'UnsupportedEncoding'),
                 ({'headers':{'Content-Type':'application/pdf'}}, 'UnsupportedContentType'),
                 ({'headers':{'Content-Length':'bad'}}, 'InvalidResponse'),
                 ({'headers':{'Content-Length':str(1024*1024+1)}}, 'ResponseTooLarge'),
                 ({'data':b'x'*(1024*1024+1)}, 'ResponseTooLarge')]
        for kwargs, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.run_response(**kwargs)['error_type'], expected)

    def test_network_errors_sanitized(self):
        for error, expected in [(TimeoutError('secret'), 'Timeout'), (OSError('secret'), 'NetworkError')]:
            result = self.run_response(error=error)
            self.assertEqual(result['error_type'], expected)
            self.assertNotIn('secret', str(result))
        with patch('safe_http.socket.getaddrinfo', side_effect=socket.gaierror('secret')):
            self.assertEqual(web_tools.fetch_url(URL)['error_type'], 'DNSFailed')

    def test_html_extraction(self):
        text = '<h1>标题</h1><script>secret</script><style>hidden</style><noscript>no</noscript><p>你好 &amp; Athena</p>'
        self.assertEqual(self.run_response(headers={'Content-Type':'text/html'}, data=text.encode())['content'], '标题\n你好 & Athena')

    def test_encoding_empty_and_truncation(self):
        for data in [b'\xff', b'\x00']:
            self.assertEqual(self.run_response(data=data)['error_type'], 'InvalidText')
        for text in ['', '中'*12000, '中'*12001]:
            result = self.run_response(data=text.encode())
            self.assertEqual(result['content'], text[:12000])
            self.assertEqual(result['truncated'], len(text)>12000)
