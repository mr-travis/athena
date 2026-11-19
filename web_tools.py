"""受限网页读取工具，可独立调用或通过模型工具分发。"""

from html.parser import HTMLParser

from safe_http import FetchError, download_text

MAX_TEXT_CHARS = 12000


class PageTextParser(HTMLParser):
    """提取基本文本，不执行脚本、不保证识别文章主体。"""

    SKIPPED = frozenset({"script", "style", "noscript"})
    BLOCKS = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skipped = []
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIPPED:
            self.skipped.append(tag)
        if not self.skipped and tag in self.BLOCKS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if self.skipped:
            if tag == self.skipped[-1]:
                self.skipped.pop()
            return
        if tag in self.BLOCKS:
            self.chunks.append("\n")
        elif tag in ("td", "th"):
            self.chunks.append(" ")

    def handle_data(self, data):
        if not self.skipped:
            self.chunks.append(data)

    def text(self):
        return "\n".join(line for raw in "".join(self.chunks).splitlines() if (line := " ".join(raw.split())))


def fetch_url(url: str) -> dict:
    """读取白名单公开网页；预期错误转为固定反馈，不返回底层异常。"""
    try:
        data, media_type = download_text(url)
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise FetchError("InvalidText", "网页必须是有效的 UTF-8 文本。") from None
        if "\x00" in text:
            raise FetchError("InvalidText", "网页文本不能包含 NUL 字符。")
        if media_type == "text/html":
            parser = PageTextParser()
            parser.feed(text)
            parser.close()
            text = parser.text()
        return {"ok": True, "url": url, "content": text[:MAX_TEXT_CHARS],
                "truncated": len(text) > MAX_TEXT_CHARS}
    except FetchError as exc:
        return {"ok": False, "error_type": exc.kind, "message": exc.message}
