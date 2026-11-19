"""受限 HTTPS 下载；不使用代理、重定向或进程全局 DNS 替换。"""

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit

ALLOWED_HOSTS = frozenset({"api-docs.deepseek.com"})
MAX_DOWNLOAD_BYTES = 1024 * 1024
SOCKET_TIMEOUT = 10


class FetchError(Exception):
    """仅携带允许返回的固定错误类别与说明。"""

    def __init__(self, kind, message):
        self.kind = kind
        self.message = message
        super().__init__(message)


def validate_url(url):
    if (not isinstance(url, str) or not url or len(url) > 4096
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
            or "\\" in url):
        raise FetchError("InvalidURL", "请提供有效的 HTTPS URL。")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise FetchError("InvalidURL", "URL 格式无效。") from None
    if (parts.scheme != "https" or parts.username is not None or parts.password is not None
            or host not in ALLOWED_HOSTS or port not in (None, 443)):
        raise FetchError("AddressNotAllowed", "仅允许白名单域名的 HTTPS 443 地址，且不能包含账号密码。")
    target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    try:
        target.encode("ascii")
    except UnicodeEncodeError:
        raise FetchError("InvalidURL", "URL 路径和查询中的非 ASCII 字符需进行百分号编码。") from None
    return host, target


def resolve_public_addresses(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not addresses:
        raise FetchError("DNSFailed", "域名解析没有返回地址。")
    for family, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if family not in (socket.AF_INET, socket.AF_INET6) or not ip.is_global or ip.is_multicast or ip.is_unspecified:
            raise FetchError("AddressNotAllowed", "域名解析包含不允许访问的地址。")
        if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo):
            raise FetchError("AddressNotAllowed", "不允许 IPv6 地址转换或隧道地址。")
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TCP 直接连接已校验的 sockaddr，TLS 仍验证原始域名。"""

    def __init__(self, host, address):
        super().__init__(host, port=443, timeout=SOCKET_TIMEOUT, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        family, socktype, protocol, _, sockaddr = self.address
        raw = socket.socket(family, socktype, protocol)
        try:
            raw.settimeout(self.timeout)
            raw.connect(sockaddr)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def download_text(url):
    """返回下载字节和媒体类型；DNS 解析不受 socket 超时的严格总时限约束。"""
    host, target = validate_url(url)
    connection = None
    try:
        addresses = resolve_public_addresses(host)
        # 第一版不自动尝试其他地址，避免隐藏重试与额外耗时。
        connection = PinnedHTTPSConnection(host, addresses[0])
        connection.request("GET", target, headers={
            "Accept": "text/html, text/plain", "Accept-Encoding": "identity",
            "User-Agent": "Athena/0.1", "Connection": "close",
        })
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise FetchError("RedirectNotAllowed", "第一版不跟随网页重定向。")
        if response.status != 200:
            raise FetchError("HTTPError", "网页未返回 HTTP 200。")
        if response.getheader("Content-Encoding", "identity").strip().lower() != "identity":
            raise FetchError("UnsupportedEncoding", "第一版不接受压缩响应。")
        content_type = response.getheader("Content-Type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type not in ("text/html", "text/plain"):
            raise FetchError("UnsupportedContentType", "仅支持 HTML 和纯文本响应。")
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                size = int(length)
            except ValueError:
                raise FetchError("InvalidResponse", "网页响应长度无效。") from None
            if size < 0:
                raise FetchError("InvalidResponse", "网页响应长度无效。")
            if size > MAX_DOWNLOAD_BYTES:
                raise FetchError("ResponseTooLarge", "网页超过 1 MiB 下载上限。")
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise FetchError("ResponseTooLarge", "网页超过 1 MiB 下载上限。")
        return data, media_type
    except FetchError:
        raise
    except socket.gaierror:
        raise FetchError("DNSFailed", "域名解析失败。") from None
    except TimeoutError:
        raise FetchError("Timeout", "网页连接或读取操作超时。") from None
    except (OSError, http.client.HTTPException):
        raise FetchError("NetworkError", "网页连接或读取失败。") from None
    finally:
        if connection is not None:
            connection.close()
