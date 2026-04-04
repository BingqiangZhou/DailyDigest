"""HTML parsing utilities (zero-dependency + BeautifulSoup fallback)."""

import re
from html.parser import HTMLParser


class HTMLStripper(HTMLParser):
    """HTML to plain text parser."""
    BLOCK_TAGS = frozenset({
        'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'blockquote', 'tr', 'ul', 'ol', 'table', 'hr', 'section', 'article'
    })
    SKIP_TAGS = frozenset({'style', 'script'})

    def __init__(self):
        super().__init__()
        self._pieces = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS and self._pieces and self._pieces[-1] != '\n':
            self._pieces.append('\n')

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK_TAGS:
            self._pieces.append('\n')

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._pieces.append(data)

    def handle_entityref(self, name):
        if self._skip_depth > 0:
            return
        entities = {
            'amp': '&', 'lt': '<', 'gt': '>', 'nbsp': '', 'quot': '"',
            'apos': "'", 'mdash': '—', 'ndash': '–', 'bull': '•'
        }
        self._pieces.append(entities.get(name, f'&{name};'))

    def handle_charref(self, name):
        if self._skip_depth > 0:
            return
        try:
            if name.startswith('x') or name.startswith('X'):
                char = chr(int(name[1:], 16))
            else:
                char = chr(int(name))
            self._pieces.append(char)
        except (ValueError, OverflowError):
            self._pieces.append(f'&#{name};')

    def get_text(self):
        text = ''.join(self._pieces)
        text = text.replace('\xa0', ' ')
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()


def strip_html(html_text):
    """将 HTML 转为纯文本（零依赖）"""
    if not html_text:
        return ""
    stripper = HTMLStripper()
    stripper.feed(html_text)
    return stripper.get_text()


def strip_html_with_bs4(html_text, max_length=2000):
    """将 HTML 转为纯文本（使用 BeautifulSoup，需要安装）"""
    if not html_text:
        return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        if len(text) > max_length:
            text = text[:max_length] + "..."
        return text
    except ImportError:
        return strip_html(html_text)
