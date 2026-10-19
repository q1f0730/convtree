#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资料入库前的「抽文本」：文件（txt/md/pdf/docx）、网页 URL、粘贴文本。

统一出口：normalize() —— 换行统一为 \\n、去 BOM，之后存进 source.content，
块偏移（chunk.start/end）就是这串文本的字符偏移。
非法输入一律抛 ValueError（上层转 4xx），不支持类型抛 UnsupportedType（→ 415）。
"""
from __future__ import print_function

import io
import re
from html.parser import HTMLParser

MAX_BYTES = 20 * 1024 * 1024
URL_MAX_BYTES = 5 * 1024 * 1024
URL_TIMEOUT = 15.0
TEXT_EXTS = ("txt", "md", "markdown", "text")
FILE_EXTS = TEXT_EXTS + ("pdf", "docx")


class UnsupportedType(ValueError):
    pass


def normalize(text):
    text = (text or "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def decode_text(raw):
    """UTF-8（含 BOM）优先，Windows 中文常见的 GB18030 次之，最后容错替换。"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def ext_of(filename):
    fn = (filename or "").strip().lower()
    return fn.rsplit(".", 1)[-1] if "." in fn else ""


def extract_pdf(raw):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages)


def extract_docx(raw):
    from docx import Document
    doc = Document(io.BytesIO(raw))
    return "\n".join(p.text for p in doc.paragraphs)


def extract_file(filename, raw):
    """返回 (type, text)。"""
    if not raw:
        raise ValueError("文件为空")
    if len(raw) > MAX_BYTES:
        raise ValueError("文件过大（上限 20MB）")
    ext = ext_of(filename)
    if ext not in FILE_EXTS:
        raise UnsupportedType("不支持的文件类型：%s（支持 txt/md/pdf/docx）" % (ext or "无扩展名"))
    if ext == "pdf":
        try:
            text = extract_pdf(raw)
        except Exception:
            raise ValueError("PDF 无法解析")
    elif ext == "docx":
        try:
            text = extract_docx(raw)
        except Exception:
            raise ValueError("Word 文档无法解析")
    else:
        text = decode_text(raw)
        ext = "md" if ext in ("md", "markdown") else "txt"
    return ext, normalize(text)


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "nav", "footer"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header",
             "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr", "dd", "dt", "main"}

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.parts, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag == "body":  # 漏写 </head> 的页面：进 body 即解除 head 屏蔽
            self.skip = 0
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in ("br", "hr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.skip:
            self.parts.append(data)


def html_to_text(html):
    """返回 (title, text)。stdlib html.parser，去 script/style/导航等。"""
    p = _HTMLText()
    p.feed(html or "")
    p.close()
    raw = "".join(p.parts)
    lines = [re.sub(r"[ \t\u00a0\u3000]+", " ", ln).strip() for ln in raw.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\s+", " ", p.title).strip(), normalize(text)


def fetch_url(url):
    """抓网页，返回 (content_type, bytes)。可被测试 monkeypatch。失败抛 ValueError。"""
    import httpx
    try:
        with httpx.Client(timeout=URL_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 convtree/0.1"}) as cli:
            r = cli.get(url)
    except Exception as exc:
        raise ValueError("URL 抓取失败：%s" % type(exc).__name__)
    if r.status_code >= 400:
        raise ValueError("URL 抓取失败：HTTP %d" % r.status_code)
    body = r.content
    if len(body) > URL_MAX_BYTES:
        raise ValueError("网页过大（上限 5MB）")
    return r.headers.get("content-type", ""), body


def extract_url(url):
    """返回 (type, name, text)。"""
    url = (url or "").strip()
    if not re.match(r"^https?://[^\s/]+", url, re.I):
        raise ValueError("URL 非法（需 http/https）")
    ctype, body = fetch_url(url)
    ctype = (ctype or "").lower()
    if not body:
        raise ValueError("网页内容为空")
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
        try:
            return "pdf", url, normalize(extract_pdf(body))
        except Exception:
            raise ValueError("PDF 无法解析")
    m = re.search(r"charset=([\w-]+)", ctype)
    text = None
    if m:
        try:
            text = body.decode(m.group(1))
        except (LookupError, UnicodeDecodeError):
            text = None
    if text is None:
        text = decode_text(body)
    if "html" in ctype or re.search(r"<html|<body|<p[\s>]", text[:5000], re.I):
        title, plain = html_to_text(text)
        return "url", (title or url)[:120], plain
    return "url", url, normalize(text)
