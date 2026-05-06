import re

_SECTION_RE = re.compile(
    r'^(?:'
    r'(?:chapter|ch\.?)\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten'
    r'|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty'
    r'|twenty.one|twenty.two|twenty.three|thirty|forty|fifty|sixty|seventy|eighty|ninety'
    r'|hundred)'
    r'|part\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five)'
    r'|prologue|epilogue|foreword|preface|introduction|afterword|appendix|interlude'
    r'|chapter\s+\w+'
    r')\b.*$',
    re.IGNORECASE
)


def _looks_like_heading(line):
    line = line.strip()
    if not line:
        return False
    if len(line) > 150:
        return False
    if _SECTION_RE.match(line):
        return True
    # All-caps short line
    if line.isupper() and 2 < len(line) < 80:
        return True
    return False


def parse(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        raw = f.read()

    lines = raw.splitlines()

    # Try to extract title from first non-empty lines
    title = 'Unknown Title'
    author = 'Unknown Author'
    for line in lines[:20]:
        line = line.strip()
        if line and len(line) < 120:
            title = line
            break

    # Detect "by Author" pattern
    by_match = re.search(r'\bby\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', raw[:500])
    if by_match:
        author = by_match.group(1)

    chapters = []
    current_title = title
    current_lines = []
    order = 0

    for line in lines:
        stripped = line.strip()
        if _looks_like_heading(stripped):
            content = '\n'.join(current_lines).strip()
            if len(content) > 100:
                chapters.append({
                    'title': current_title,
                    'order_num': order,
                    'content': content,
                    'word_count': len(content.split()),
                })
                order += 1
            current_title = stripped
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        content = '\n'.join(current_lines).strip()
        if len(content) > 50:
            chapters.append({
                'title': current_title,
                'order_num': order,
                'content': content,
                'word_count': len(content.split()),
            })

    if not chapters:
        chapters = [{
            'title': title,
            'order_num': 0,
            'content': raw.strip(),
            'word_count': len(raw.split()),
        }]

    return {
        'title': title,
        'author': author,
        'language': 'en',
        'cover_b64': None,
        'chapters': chapters,
    }
