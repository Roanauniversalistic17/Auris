"""
Text enrichment engine.

Splits chapter text into TTS-ready segments, attributes dialogue to characters,
injects OmniVoice non-verbal tags, adjusts speed for scene tone, and avoids
common sentence-boundary mistakes such as "Mr." or "Dr." being treated as a
full stop.
"""

import re

_DOT = "<prd>"
_ELLIPSIS = "<ell>"
_SPLIT = "<split>"
_QUOTE_CLASS = r'["\u201c\u201d]'
_QUOTE_CONTENT_CLASS = r'"\u201c\u201d'
_NAME_PATTERN = r"[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){0,2}"

_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:chapter|ch\.?)\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|"
    r"eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen|eighteen|nineteen|twenty(?:\s*-\s*\w+)?)"
    r"|part\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"|prologue|epilogue|foreword|preface|introduction|afterword|appendix|interlude"
    r")\b.*$",
    re.IGNORECASE,
)

_SHORT_ALL_CAPS_RE = re.compile(r"^[A-Z0-9][A-Z0-9 '&,:;.-]{1,80}$")
_QUESTION_RE = re.compile(r'\?\s*["\u201d]?\s*$')
_SURPRISE_RE = re.compile(r'!\s*["\u201d]?\s*$')

_QUESTION_HINT_RE = re.compile(
    r"\b(asked|wondered|queried|questioned|inquired)\b",
    re.IGNORECASE,
)
_SURPRISE_HINT_RE = re.compile(
    r"\b(gasp|gasped|gasping|exclaim|exclaimed|cried out|startled|shouted|yelled|"
    r"yelped|screamed)\b",
    re.IGNORECASE,
)
_LAUGHTER_RE = re.compile(r"\b(laugh|laughs|laughed|laughing|chuckl|giggl)\b", re.IGNORECASE)
_SIGH_RE = re.compile(r"\b(sigh|sighs|sighed|sighing|exhale|exhaled)\b", re.IGNORECASE)
_DISSATISFACTION_RE = re.compile(
    r"\b(grumbl|mutter|growl|snapp|barked|hiss|scowl|gritted)\b",
    re.IGNORECASE,
)
_CONFIRMATION_RE = re.compile(
    r"\b(nod|nodded|agreed|confirm|confirmed|affirm|affirmed|yes|indeed)\b",
    re.IGNORECASE,
)
_WHISPER_RE = re.compile(
    r"\b(whisper|whispered|breathed|murmured|under his breath|under her breath)\b",
    re.IGNORECASE,
)

_TAG_RULES = [
    (_LAUGHTER_RE, "[laughter]"),
    (_SIGH_RE, "[sigh]"),
    (_DISSATISFACTION_RE, "[dissatisfaction-hnn]"),
    (_CONFIRMATION_RE, "[confirmation-en]"),
]
_ATTRIBUTION_VERBS = (
    "said|replied|asked|whispered|shouted|cried|muttered|exclaimed|called|added|"
    "continued|laughed|sighed|groaned|snapped|retorted|insisted|demanded|pleaded|"
    "began|noted|observed|remarked|growled|yelled|murmured|gasped|stammered|shrieked"
)
_ATTRIBUTION_SENTENCE_RE = re.compile(
    rf"^(?:{_NAME_PATTERN}|he|she|they)\s+(?:{_ATTRIBUTION_VERBS})(?:\s+\w+){{0,4}}[.!?]?$",
    re.IGNORECASE,
)

_DIALOGUE_RE = re.compile(
    rf"(?:"
    rf"{_QUOTE_CLASS}(?P<text1>[^{_QUOTE_CONTENT_CLASS}]{{2,}}){_QUOTE_CLASS}\s*[,.]?\s*"
    rf"(?P<name1>{_NAME_PATTERN})\s+"
    rf"(?:{_ATTRIBUTION_VERBS})"
    rf"|"
    rf"(?P<name2>{_NAME_PATTERN})\s+"
    rf"(?:{_ATTRIBUTION_VERBS})\s*[,.]?\s*"
    rf"{_QUOTE_CLASS}(?P<text2>[^{_QUOTE_CONTENT_CLASS}]{{2,}}){_QUOTE_CLASS}"
    rf")",
    re.DOTALL,
)

_STANDALONE_QUOTE_RE = re.compile(rf'{_QUOTE_CLASS}([^"\u201c\u201d]{{2,}}){_QUOTE_CLASS}')

_ACTION_WORDS = re.compile(
    r"\b(ran|rushed|sprinted|struck|fell|crashed|burst|grabbed|pulled|pushed|"
    r"slammed|exploded|screamed|fired|attacked|fled|chased|leaped|jumped|"
    r"stabbed|shot|hit|smashed|broke|shattered)\b",
    re.IGNORECASE,
)
_SLOW_WORDS = re.compile(
    r"\b(slowly|gently|quietly|softly|carefully|tenderly|silently|"
    r"solemnly|mournfully|peacefully|dreamily)\b",
    re.IGNORECASE,
)

_ABBREVIATION_WORDS = (
    "Mr", "Mrs", "Ms", "Dr", "Prof", "Sr", "Jr", "St", "Mt", "Lt", "Capt",
    "Col", "Gen", "Sgt", "Rev", "Hon", "Pres", "Gov", "Sen", "Rep", "Supt",
    "Det", "No", "Nos", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug",
    "Sep", "Sept", "Oct", "Nov", "Dec", "etc", "vs",
)
_ABBREVIATION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(item) for item in _ABBREVIATION_WORDS) + r")\.",
    re.IGNORECASE,
)
_MULTI_DOT_TOKEN_RE = re.compile(
    r"\b(?:e\.g\.|i\.e\.|a\.m\.|p\.m\.|u\.s\.a?\.|u\.k\.|u\.n\.|ph\.d\.)",
    re.IGNORECASE,
)
_INITIALISM_RE = re.compile(r"\b(?:[A-Z]\.){2,}")
_NAME_INITIAL_RE = re.compile(r"\b[A-Z]\.(?=\s+[A-Z][a-z])")


def _scene_speed(text: str) -> float:
    action = len(_ACTION_WORDS.findall(text))
    slow = len(_SLOW_WORDS.findall(text))
    if action >= 3:
        return 1.15
    if slow >= 2:
        return 0.9
    return 1.0


def _title_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())).strip()


def _is_short_all_caps_heading(text: str) -> bool:
    words = text.split()
    return len(words) <= 10 and bool(_SHORT_ALL_CAPS_RE.match(text))


def _is_heading_paragraph(text: str, chapter_title: str | None = None) -> bool:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return False
    if chapter_title and _title_key(cleaned) == _title_key(chapter_title):
        return True
    if _SECTION_HEADING_RE.match(cleaned):
        return True
    if _is_short_all_caps_heading(cleaned) and not re.search(r"[.!?]", cleaned):
        return True
    return False


def _line_starts_new_paragraph(previous_line: str, current_line: str) -> bool:
    prev = previous_line.rstrip()
    curr = current_line.lstrip()
    if not prev:
        return True
    if _is_heading_paragraph(curr):
        return True
    return bool(re.search(r'[.!?]["\u201d]?\s*$', prev))


def _split_paragraphs(text: str, chapter_title: str | None = None) -> list[str]:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n+", raw)
    paragraphs: list[str] = []

    for block in blocks:
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in block.splitlines()
            if re.sub(r"\s+", " ", line).strip()
        ]
        if not lines:
            continue

        buffer: list[str] = []
        for line in lines:
            if _is_heading_paragraph(line, chapter_title):
                if buffer:
                    paragraphs.append(" ".join(buffer).strip())
                    buffer = []
                paragraphs.append(line)
                continue
            if buffer and _line_starts_new_paragraph(buffer[-1], line):
                paragraphs.append(" ".join(buffer).strip())
                buffer = [line]
            else:
                buffer.append(line)
        if buffer:
            paragraphs.append(" ".join(buffer).strip())

    cleaned: list[str] = []
    for idx, paragraph in enumerate(paragraphs):
        if _is_heading_paragraph(paragraph, chapter_title):
            continue
        if idx < 2 and _is_short_all_caps_heading(paragraph):
            continue
        cleaned.append(paragraph)

    return cleaned


def _protect_sentence_boundaries(text: str) -> str:
    protected = text.replace("...", _ELLIPSIS)
    protected = re.sub(r"(?<=\d)\.(?=\d)", _DOT, protected)
    protected = _MULTI_DOT_TOKEN_RE.sub(lambda match: match.group(0).replace(".", _DOT), protected)
    protected = _INITIALISM_RE.sub(lambda match: match.group(0).replace(".", _DOT), protected)
    protected = _NAME_INITIAL_RE.sub(lambda match: match.group(0).replace(".", _DOT), protected)
    protected = _ABBREVIATION_RE.sub(lambda match: match.group(0).replace(".", _DOT), protected)
    return protected


def _restore_sentence_boundaries(text: str) -> str:
    return text.replace(_DOT, ".").replace(_ELLIPSIS, "...")


def _split_paragraph_sentences(paragraph: str) -> list[str]:
    protected = _protect_sentence_boundaries(paragraph)
    protected = re.sub(
        r'([.!?]["\u201d]?)\s+(?=(?:["\u201c]?[A-Z0-9]))',
        rf"\1{_SPLIT}",
        protected,
    )
    parts = [_restore_sentence_boundaries(part).strip() for part in protected.split(_SPLIT)]
    return [part for part in parts if part]


def _has_dialogue(text: str) -> bool:
    return bool(_STANDALONE_QUOTE_RE.search(text))


def _should_merge_sentences(buffer: str, sentence: str) -> bool:
    if not buffer or not sentence:
        return False
    if _has_dialogue(buffer) and _ATTRIBUTION_SENTENCE_RE.match(sentence):
        return True
    if _ATTRIBUTION_SENTENCE_RE.match(buffer) and _has_dialogue(sentence):
        return True
    if _QUESTION_RE.search(buffer) or _SURPRISE_RE.search(buffer):
        return False
    if _has_dialogue(buffer) != _has_dialogue(sentence):
        return False
    combined_words = len((buffer + " " + sentence).split())
    if combined_words > 30:
        return False
    if len(buffer.split()) < 8 or len(sentence.split()) < 4:
        return True
    return not _has_dialogue(buffer) and not _has_dialogue(sentence) and combined_words <= 22


def _split_sentences(text: str, chapter_title: str | None = None) -> list[str]:
    paragraphs = _split_paragraphs(text, chapter_title)
    segments: list[str] = []

    for paragraph in paragraphs:
        sentences = _split_paragraph_sentences(paragraph)
        if not sentences:
            continue

        buffer = ""
        for sentence in sentences:
            if not buffer:
                buffer = sentence
            elif _should_merge_sentences(buffer, sentence):
                buffer = f"{buffer} {sentence}".strip()
            else:
                segments.append(buffer)
                buffer = sentence
        if buffer:
            segments.append(buffer)

    return segments


def _build_dialogue_map(text: str) -> dict[str, str]:
    """Map dialogue snippets to detected speaker names."""
    mapping = {}
    for match in _DIALOGUE_RE.finditer(text):
        dialogue = match.group("text1") or match.group("text2") or ""
        speaker = match.group("name1") or match.group("name2") or ""
        if dialogue and speaker:
            mapping[dialogue.strip()[:60]] = speaker.strip()
    return mapping


def _find_speaker(sentence: str, dialogue_map: dict, last_speaker: str | None) -> str | None:
    if not sentence:
        return None

    inner = _STANDALONE_QUOTE_RE.search(sentence)
    if inner:
        snippet = inner.group(1).strip()[:60]
        for key, name in dialogue_map.items():
            if key in snippet or snippet in key:
                return name

    return None


def _select_expression_tag(sentence: str, context: str) -> str | None:
    if _QUESTION_RE.search(sentence) or _QUESTION_HINT_RE.search(context):
        return "[question-en]"
    for pattern, tag in _TAG_RULES:
        if pattern.search(context):
            return tag
    if _SURPRISE_HINT_RE.search(context) or (
        _SURPRISE_RE.search(sentence)
        and re.search(r"\b(oh|ah|what|look|no|watch out|help)\b", context, re.IGNORECASE)
    ):
        return "[surprise-oh]"
    return None


def _inject_tags(sentence: str, context: str) -> tuple[str, str | None]:
    tag = _select_expression_tag(sentence, context)
    if not tag:
        return sentence, None

    inner = _STANDALONE_QUOTE_RE.search(sentence)
    if inner:
        quoted = inner.group(1).strip()
        enriched_quoted = f"{tag} {quoted}".strip()
        sentence = sentence[:inner.start(1)] + enriched_quoted + sentence[inner.end(1):]
    else:
        sentence = f"{tag} {sentence}".strip()

    return sentence, tag


def _segment_speed(sentence: str, is_dialogue: bool, scene_speed: float, tag: str | None, is_whisper: bool) -> float:
    speed = scene_speed if is_dialogue else min(scene_speed, 1.05)

    if is_whisper or tag == "[sigh]":
        speed = min(speed, 0.94)
    elif tag in {"[surprise-oh]", "[laughter]"} and is_dialogue:
        speed = max(speed, 1.03)

    if "..." in sentence or " -- " in sentence or ";" in sentence or ":" in sentence:
        speed = min(speed, 0.98)

    return round(speed, 2)


def enrich_chapter(
    chapter_text: str,
    character_map: dict,
    narrator_instruct: str = "male, elderly, low pitch, british accent",
    single_narrator_mode: bool = False,
    chapter_title: str | None = None,
) -> list[dict]:
    """
    Return segment dicts used by playback and export.
    """
    cleaned_text = str(chapter_text or "").strip()
    dialogue_map = _build_dialogue_map(cleaned_text)
    sentences = _split_sentences(cleaned_text, chapter_title=chapter_title)
    scene_speed = _scene_speed(cleaned_text)

    segments = []
    last_speaker = None

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        is_dialogue = _has_dialogue(sentence)
        speaker = _find_speaker(sentence, dialogue_map, last_speaker) if is_dialogue else None
        if speaker and speaker not in character_map:
            speaker = None
        if speaker:
            last_speaker = speaker

        is_whisper = bool(_WHISPER_RE.search(sentence))
        character_name = None if single_narrator_mode else speaker
        if single_narrator_mode:
            instruct = narrator_instruct
        else:
            instruct = (
                character_map.get(speaker, {}).get("instruct", narrator_instruct)
                if speaker
                else narrator_instruct
            )

        if is_whisper and single_narrator_mode:
            if "whisper" not in narrator_instruct:
                instruct = narrator_instruct + ", whisper"
        elif is_whisper and speaker and speaker in character_map:
            base = character_map[speaker].get("instruct", narrator_instruct)
            if "whisper" not in base:
                instruct = base + ", whisper"

        enriched, tag = _inject_tags(sentence, sentence)
        speed = _segment_speed(sentence, is_dialogue, scene_speed, tag, is_whisper)

        segments.append(
            {
                "text": sentence,
                "enriched_text": enriched,
                "character_name": character_name,
                "instruct": instruct,
                "speed": speed,
                "is_dialogue": is_dialogue,
                "is_whisper": is_whisper,
            }
        )

    return segments
