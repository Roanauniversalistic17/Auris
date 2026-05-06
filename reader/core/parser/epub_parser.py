import base64
import re
from html.parser import HTMLParser

try:
    import ebooklib
    from ebooklib import epub
    EBOOKLIB_OK = True
except ImportError:
    EBOOKLIB_OK = False


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self._skip_tags = {'script', 'style'}
        self._current_skip = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._skip_tags:
            self._current_skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in self._skip_tags:
            self._current_skip = max(0, self._current_skip - 1)

    def handle_data(self, data):
        if self._current_skip == 0:
            self.text_parts.append(data)

    def get_text(self):
        return ' '.join(self.text_parts)


def _strip_html(html_content):
    stripper = _HTMLStripper()
    stripper.feed(html_content)
    raw = stripper.get_text()
    raw = re.sub(r'\s+', ' ', raw).strip()
    return raw


def _decode_item(item):
    try:
        content = item.get_content()
        return content.decode('utf-8', errors='replace')
    except Exception:
        return ''


def parse(file_path):
    if not EBOOKLIB_OK:
        raise ImportError("ebooklib is not installed. Run: pip install ebooklib")

    book = epub.read_epub(file_path, options={'ignore_ncx': False})

    title = book.get_metadata('DC', 'title')
    title = title[0][0] if title else 'Unknown Title'

    author = book.get_metadata('DC', 'creator')
    author = author[0][0] if author else 'Unknown Author'

    language = book.get_metadata('DC', 'language')
    language = language[0][0][:2] if language else 'en'

    cover_b64 = None
    for item in book.get_items_of_type(ebooklib.ITEM_COVER):
        try:
            cover_b64 = base64.b64encode(item.get_content()).decode()
            break
        except Exception:
            pass
    if not cover_b64:
        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
            name = item.get_name().lower()
            if 'cover' in name:
                try:
                    cover_b64 = base64.b64encode(item.get_content()).decode()
                    break
                except Exception:
                    pass

    # Build spine order
    spine_ids = [s[0] for s in book.spine]
    spine_items = []
    for sid in spine_ids:
        item = book.get_item_with_id(sid)
        if item and item.get_type() == ebooklib.ITEM_DOCUMENT:
            spine_items.append(item)

    chapters = []
    for order, item in enumerate(spine_items):
        html = _decode_item(item)
        text = _strip_html(html)
        if len(text.strip()) < 50:
            continue

        # Try to extract heading as chapter title
        heading_match = re.search(
            r'<h[1-3][^>]*>(.*?)</h[1-3]>', html, re.IGNORECASE | re.DOTALL
        )
        if heading_match:
            ch_title = _strip_html(heading_match.group(1)).strip()
        else:
            ch_title = f'Section {order + 1}'

        chapters.append({
            'title': ch_title,
            'order_num': order,
            'content': text,
            'word_count': len(text.split()),
        })

    return {
        'title': title,
        'author': author,
        'language': language,
        'cover_b64': cover_b64,
        'chapters': chapters,
    }
