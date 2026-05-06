"""
Text enrichment engine.

Splits chapter text into TTS segments, attributes dialogue to characters,
injects OmniVoice non-verbal tags, adjusts speed for scene tone, and
signals when whisper mode should activate.
"""

import re

# ── Non-verbal tag triggers ───────────────────────────────────────────────────

_TAG_RULES = [
    # (pattern in surrounding narration, tag to prepend)
    (re.compile(r'\b(laugh|laughs|laughed|laughing|chuckl|giggl)\b', re.I), '[laughter]'),
    (re.compile(r'\b(sigh|sighs|sighed|sighing|exhale|exhaled)\b', re.I), '[sigh]'),
    (re.compile(r'\b(gasp|gasped|gasping|exclaim|cried out|startled)\b', re.I), '[surprise-oh]'),
    (re.compile(r'\b(grumbl|mutter|growl|snapp|barked|hiss)\b', re.I), '[dissatisfaction-hnn]'),
    (re.compile(r'\b(nod|nodded|agreed|confirm|affirm|right\b)\b', re.I), '[confirmation-en]'),
]

_WHISPER_RE = re.compile(r'\b(whisper|whispered|breathed|murmured|breathes)\b', re.I)
_QUESTION_RE = re.compile(r'\?\s*$')

# ── Dialogue extraction ───────────────────────────────────────────────────────

# Matches: "Dialogue text," Character said / Character said, "Dialogue text"
_DIALOGUE_RE = re.compile(
    r'(?:'
    r'["""](?P<text1>[^"""]{2,})["""]\s*[,.]?\s*(?P<name1>[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+'
    r'(?:said|replied|asked|whispered|shouted|cried|muttered|exclaimed|called|added|'
    r'continued|laughed|sighed|groaned|snapped|retorted|insisted|demanded|pleaded|'
    r'began|noted|observed|remarked)'
    r'|'
    r'(?P<name2>[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+'
    r'(?:said|replied|asked|whispered|shouted|cried|muttered|exclaimed|called|added|'
    r'continued|laughed|sighed|groaned|snapped|retorted|insisted|demanded|pleaded|'
    r'began|noted|observed|remarked)\s*[,.]?\s*["""](?P<text2>[^"""]{2,})["""]'
    r')',
    re.DOTALL
)

# Standalone quoted text (no explicit attribution)
_STANDALONE_QUOTE_RE = re.compile(r'["""]([^"""]{2,})["""]')

# ── Scene tone for speed adjustment ──────────────────────────────────────────

_ACTION_WORDS = re.compile(
    r'\b(ran|rushed|sprinted|struck|fell|crashed|burst|grabbed|pulled|pushed|'
    r'slammed|exploded|screamed|fired|attacked|fled|chased|leaped|jumped|'
    r'stabbed|shot|hit|smashed|broke|shattered)\b', re.I
)
_SLOW_WORDS = re.compile(
    r'\b(slowly|gently|quietly|softly|carefully|tenderly|silently|'
    r'solemnly|mournfully|peacefully|dreamily)\b', re.I
)


def _scene_speed(text: str) -> float:
    action = len(_ACTION_WORDS.findall(text))
    slow = len(_SLOW_WORDS.findall(text))
    if action >= 3:
        return 1.15
    if slow >= 2:
        return 0.9
    return 1.0


# ── Sentence splitter ─────────────────────────────────────────────────────────

_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z""])')


def _split_sentences(text: str) -> list[str]:
    text = text.replace('\n', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    sentences = _SENTENCE_RE.split(text)
    # Re-merge very short fragments
    merged = []
    buf = ''
    for s in sentences:
        buf = (buf + ' ' + s).strip() if buf else s
        if len(buf) >= 40:
            merged.append(buf)
            buf = ''
    if buf:
        if merged:
            merged[-1] += ' ' + buf
        else:
            merged.append(buf)
    return merged


# ── Attribution cache from dialogue map ──────────────────────────────────────

def _build_dialogue_map(text: str) -> dict[str, str]:
    """Maps dialogue snippet → speaker name."""
    mapping = {}
    for m in _DIALOGUE_RE.finditer(text):
        dialogue = m.group('text1') or m.group('text2') or ''
        speaker = m.group('name1') or m.group('name2') or ''
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


# ── Main enrichment function ──────────────────────────────────────────────────

def enrich_chapter(
    chapter_text: str,
    character_map: dict,        # name → {instruct, color_hex, ...}
    narrator_instruct: str = 'female, middle-aged, moderate pitch, american accent',
) -> list[dict]:
    """
    Returns a list of segment dicts:
    {
        text: str,                  # original sentence
        enriched_text: str,         # sentence with OmniVoice tags injected
        character_name: str | None, # None = narrator
        instruct: str,              # OmniVoice instruct string
        speed: float,
        is_dialogue: bool,
        is_whisper: bool,
    }
    """
    dialogue_map = _build_dialogue_map(chapter_text)
    sentences = _split_sentences(chapter_text)
    scene_speed = _scene_speed(chapter_text)

    segments = []
    last_speaker = None

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        is_dialogue = bool(_STANDALONE_QUOTE_RE.search(sentence))
        speaker = _find_speaker(sentence, dialogue_map, last_speaker) if is_dialogue else None
        if speaker and speaker not in character_map:
            speaker = None
        if speaker:
            last_speaker = speaker

        is_whisper = bool(_WHISPER_RE.search(sentence))
        character_name = speaker
        instruct = character_map.get(speaker, {}).get('instruct', narrator_instruct) if speaker else narrator_instruct

        if is_whisper and speaker and speaker in character_map:
            base = character_map[speaker].get('instruct', narrator_instruct)
            if 'whisper' not in base:
                instruct = base + ', whisper'

        # Determine surrounding narration (the sentence itself for standalone cases)
        enriched = _inject_tags(sentence, sentence)

        speed = scene_speed if is_dialogue else min(scene_speed, 1.05)

        segments.append({
            'text': sentence,
            'enriched_text': enriched,
            'character_name': character_name,
            'instruct': instruct,
            'speed': speed,
            'is_dialogue': is_dialogue,
            'is_whisper': is_whisper,
        })

    return segments


def _inject_tags(sentence: str, context: str) -> str:
    tag = None

    # Question mark → question tag
    if _QUESTION_RE.search(sentence):
        tag = '[question-en]'
    else:
        for pattern, t in _TAG_RULES:
            if pattern.search(context):
                tag = t
                break

    if tag:
        inner = _STANDALONE_QUOTE_RE.search(sentence)
        if inner:
            # Inject tag inside the quoted dialogue
            quoted = inner.group(1)
            enriched_quoted = f'{tag} {quoted}'
            sentence = sentence[:inner.start(1)] + enriched_quoted + sentence[inner.end(1):]
        else:
            sentence = f'{tag} {sentence}'

    return sentence
