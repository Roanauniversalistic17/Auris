"""
Offline Ebook Reader — Flask application.
"""

import base64
import logging
import os
import threading

from flask import (
    Flask, jsonify, render_template, request,
    send_file,
)

from core.database import init_db, get_conn
from core.tts_engine import TTSEngine
from core import characters as char_module
from core import enrichment, exporter, structure, settings as app_settings
from core.parser import epub_parser, pdf_parser, txt_parser

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

tts = TTSEngine()

DEFAULT_NARRATOR_INSTRUCT = 'female, middle-aged, moderate pitch, american accent'
VOICE_PREVIEW_TEXT = (
    'Hello. This is a voice preview sample. The afternoon is calm, the room is quiet, '
    'and every word should sound clear, steady, and natural.'
)


# ════════════════════════════════════════════════════════════════════════════
# Startup
# ════════════════════════════════════════════════════════════════════════════

@app.before_request
def _startup():
    # Only run once
    app.before_request_funcs[None].remove(_startup)
    init_db()
    tts.load_async()


def _default_narrator_instruct() -> str:
    return app_settings.get('narrator_instruct', DEFAULT_NARRATOR_INSTRUCT)


def _book_narrator_instruct(book: dict | None) -> str:
    if not book:
        return _default_narrator_instruct()
    return book.get('narrator_instruct') or _default_narrator_instruct()


def _book_single_narrator_mode(book: dict | None) -> bool:
    if not book:
        return False
    return bool(book.get('single_narrator_mode'))


def _load_book(book_id: int):
    with get_conn() as conn:
        return conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone()


def _clear_book_tts_segments(book_id: int):
    with get_conn() as conn:
        conn.execute('DELETE FROM tts_segments WHERE book_id=?', (book_id,))


def _build_segments_for_chapter(book_id: int, chapter_id: int) -> list[dict]:
    with get_conn() as conn:
        ch = conn.execute(
            'SELECT * FROM chapters WHERE id=? AND book_id=?',
            (chapter_id, book_id)
        ).fetchone()
        chars = conn.execute(
            'SELECT * FROM characters WHERE book_id=?',
            (book_id,)
        ).fetchall()
        book = conn.execute(
            'SELECT narrator_instruct, single_narrator_mode FROM books WHERE id=?',
            (book_id,)
        ).fetchone()

    if not ch:
        return []

    char_map = {r['name']: dict(r) for r in chars}
    segs = enrichment.enrich_chapter(
        ch['content'],
        char_map,
        _book_narrator_instruct(dict(book) if book else None),
        single_narrator_mode=_book_single_narrator_mode(dict(book) if book else None),
    )
    _store_segments(book_id, chapter_id, segs)
    return segs


# ════════════════════════════════════════════════════════════════════════════
# Page routes
# ════════════════════════════════════════════════════════════════════════════

@app.route('/')
def library_page():
    return render_template('library.html')


@app.route('/reader/<int:book_id>')
def reader_page(book_id):
    book = _load_book(book_id)
    if not book:
        return 'Book not found', 404
    book_data = dict(book)
    book_data['narrator_instruct'] = _book_narrator_instruct(book_data)
    book_data['single_narrator_mode'] = _book_single_narrator_mode(book_data)
    return render_template('reader.html', book=book_data)


@app.route('/voice-studio/<int:book_id>')
def voice_studio_page(book_id):
    book = _load_book(book_id)
    if not book:
        return 'Book not found', 404
    book_data = dict(book)
    book_data['narrator_instruct'] = _book_narrator_instruct(book_data)
    book_data['single_narrator_mode'] = _book_single_narrator_mode(book_data)
    return render_template('voice_studio.html', book=book_data)


# ════════════════════════════════════════════════════════════════════════════
# Book import
# ════════════════════════════════════════════════════════════════════════════

@app.route('/api/books/import', methods=['POST'])
def import_book():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Empty filename'}), 400

    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('epub', 'pdf', 'txt'):
        return jsonify({'error': f'Unsupported format: {ext}'}), 400

    dest = os.path.join(UPLOAD_DIR, f.filename)
    f.save(dest)

    try:
        if ext == 'epub':
            data = epub_parser.parse(dest)
        elif ext == 'pdf':
            data = pdf_parser.parse(dest)
        else:
            data = txt_parser.parse(dest)
    except Exception as e:
        return jsonify({'error': f'Parse error: {e}'}), 500

    chapters = structure.enrich_chapters(data['chapters'])

    with get_conn() as conn:
        cur = conn.execute(
            'INSERT INTO books (title, author, file_path, file_type, cover_b64, language, single_narrator_mode, total_chapters) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (data['title'], data['author'], dest, ext,
             data.get('cover_b64'), data.get('language', 'en'),
             int(bool(app_settings.get('single_narrator_mode', False))), len(chapters))
        )
        book_id = cur.lastrowid

        for ch in chapters:
            conn.execute(
                'INSERT INTO chapters (book_id, title, order_num, section_type, content, word_count) '
                'VALUES (?,?,?,?,?,?)',
                (book_id, ch['title'], ch['order_num'], ch.get('section_type', 'chapter'),
                 ch['content'], ch['word_count'])
            )

    # Detect characters in background
    threading.Thread(target=_detect_characters, args=(book_id, data), daemon=True).start()

    return jsonify({'book_id': book_id, 'title': data['title'], 'chapters': len(chapters)})


def _detect_characters(book_id: int, data: dict):
    full_text = ' '.join(ch['content'] for ch in data['chapters'])
    chars = char_module.extract_characters(full_text, top_n=20)
    with get_conn() as conn:
        for ch in chars:
            try:
                conn.execute(
                    'INSERT OR IGNORE INTO characters '
                    '(book_id, name, gender, frequency, instruct, color_hex) '
                    'VALUES (?,?,?,?,?,?)',
                    (book_id, ch['name'], ch['gender'], ch['frequency'],
                     ch['instruct'], ch['color_hex'])
                )
            except Exception:
                pass


# ════════════════════════════════════════════════════════════════════════════
# Library API
# ════════════════════════════════════════════════════════════════════════════

@app.route('/api/books')
def list_books():
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT b.id, b.title, b.author, b.file_type, b.cover_b64, b.added_at, '
            'b.last_read, b.total_chapters, rp.chapter_id AS progress_chapter_id, '
            'rp.position AS progress_position, c.title AS progress_chapter_title '
            'FROM books b '
            'LEFT JOIN reading_progress rp ON rp.book_id = b.id '
            'LEFT JOIN chapters c ON c.id = rp.chapter_id '
            'ORDER BY COALESCE(b.last_read, b.added_at) DESC, b.added_at DESC'
        ).fetchall()
    books = []
    for r in rows:
        d = dict(r)
        if d['cover_b64']:
            d['cover_url'] = f'/api/books/{d["id"]}/cover'
            d.pop('cover_b64')
        else:
            d['cover_url'] = None
        books.append(d)
    return jsonify(books)


@app.route('/api/books/<int:book_id>/cover')
def book_cover(book_id):
    with get_conn() as conn:
        row = conn.execute('SELECT cover_b64, file_type FROM books WHERE id=?', (book_id,)).fetchone()
    if not row or not row['cover_b64']:
        return '', 204
    img_bytes = base64.b64decode(row['cover_b64'])
    ext = 'png' if row['file_type'] == 'pdf' else 'jpeg'
    return app.response_class(img_bytes, mimetype=f'image/{ext}')


@app.route('/api/books/<int:book_id>', methods=['DELETE'])
def delete_book(book_id):
    with get_conn() as conn:
        conn.execute('DELETE FROM books WHERE id=?', (book_id,))
    return jsonify({'ok': True})


# ════════════════════════════════════════════════════════════════════════════
# Chapter API
# ════════════════════════════════════════════════════════════════════════════

@app.route('/api/books/<int:book_id>/chapters')
def list_chapters(book_id):
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT id, title, order_num, section_type, word_count FROM chapters '
            'WHERE book_id=? ORDER BY order_num',
            (book_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/books/<int:book_id>/chapters/<int:chapter_id>')
def get_chapter(book_id, chapter_id):
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM chapters WHERE id=? AND book_id=?', (chapter_id, book_id)
        ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))


@app.route('/api/books/<int:book_id>/progress', methods=['POST'])
def save_progress(book_id):
    body = request.get_json(force=True)
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO reading_progress (book_id, chapter_id, position, updated_at) '
            'VALUES (?,?,?,datetime("now")) '
            'ON CONFLICT(book_id) DO UPDATE SET chapter_id=excluded.chapter_id, '
            'position=excluded.position, updated_at=excluded.updated_at',
            (book_id, body.get('chapter_id'), body.get('position', 0))
        )
        conn.execute('UPDATE books SET last_read=datetime("now") WHERE id=?', (book_id,))
    return jsonify({'ok': True})


@app.route('/api/books/<int:book_id>/progress')
def get_progress(book_id):
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM reading_progress WHERE book_id=?', (book_id,)
        ).fetchone()
    return jsonify(dict(row) if row else {})


# ════════════════════════════════════════════════════════════════════════════
# Characters API
# ════════════════════════════════════════════════════════════════════════════

@app.route('/api/books/<int:book_id>/characters')
def list_characters(book_id):
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT * FROM characters WHERE book_id=? ORDER BY frequency DESC',
            (book_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/books/<int:book_id>/characters/<int:char_id>', methods=['PUT'])
def update_character(book_id, char_id):
    body = request.get_json(force=True)
    allowed = {'instruct', 'gender', 'color_hex', 'ref_audio_path'}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        return jsonify({'error': 'Nothing to update'}), 400
    set_clause = ', '.join(f'{k}=?' for k in updates)
    with get_conn() as conn:
        conn.execute(
            f'UPDATE characters SET {set_clause} WHERE id=? AND book_id=?',
            (*updates.values(), char_id, book_id)
        )
    _clear_book_tts_segments(book_id)
    return jsonify({'ok': True, 'segments_cleared': True})


@app.route('/api/books/<int:book_id>/characters/<int:char_id>/preview', methods=['POST'])
def preview_character(book_id, char_id):
    body = request.get_json(silent=True) or {}
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM characters WHERE id=? AND book_id=?',
                           (char_id, book_id)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    status = tts.status()
    if status['state'] != 'ready':
        return jsonify({'error': 'Model not ready', 'status': status}), 503

    instruct = (body.get('instruct') or row['instruct'] or '').strip()
    ref_audio = row['ref_audio_path'] if row['ref_audio_path'] else None
    sample_text = (
        f'Hello. I am {row["name"]}. '
        'This preview should sound clear, steady, and easy to understand.'
    )

    try:
        result = tts.generate_preview(
            instruct=instruct,
            sample_text=sample_text,
            ref_audio=ref_audio,
        )
        return jsonify({'audio_url': f'/api/audio/{result["cache_key"]}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/books/<int:book_id>/narrator', methods=['GET'])
def get_narrator(book_id):
    book = _load_book(book_id)
    if not book:
        return jsonify({'error': 'Not found'}), 404
    book_data = dict(book)
    return jsonify({
        'instruct': _book_narrator_instruct(book_data),
        'single_narrator_mode': _book_single_narrator_mode(book_data),
    })


@app.route('/api/books/<int:book_id>/narrator', methods=['PUT'])
def update_narrator(book_id):
    body = request.get_json(force=True) or {}
    book = _load_book(book_id)
    if not book:
        return jsonify({'error': 'Not found'}), 404
    book_data = dict(book)

    raw_instruct = body.get('instruct')
    instruct = (
        raw_instruct.strip()
        if isinstance(raw_instruct, str)
        else _book_narrator_instruct(book_data)
    )
    if not instruct:
        return jsonify({'error': 'Narrator instruct is required'}), 400

    raw_mode = body.get('single_narrator_mode', _book_single_narrator_mode(book_data))
    if isinstance(raw_mode, str):
        single_narrator_mode = raw_mode.strip().lower() in {'1', 'true', 'yes', 'on'}
    else:
        single_narrator_mode = bool(raw_mode)
    narrator_changed = instruct != _book_narrator_instruct(book_data)
    mode_changed = single_narrator_mode != _book_single_narrator_mode(book_data)

    with get_conn() as conn:
        conn.execute(
            'UPDATE books SET narrator_instruct=?, single_narrator_mode=? WHERE id=?',
            (instruct, int(single_narrator_mode), book_id)
        )

    if narrator_changed or mode_changed:
        _clear_book_tts_segments(book_id)

    return jsonify({
        'ok': True,
        'instruct': instruct,
        'single_narrator_mode': single_narrator_mode,
        'segments_cleared': narrator_changed or mode_changed,
    })


@app.route('/api/books/<int:book_id>/characters/narrator/preview', methods=['POST'])
def preview_narrator(book_id):
    body = request.get_json(silent=True) or {}
    book = _load_book(book_id)
    if not book:
        return jsonify({'error': 'Not found'}), 404

    status = tts.status()
    if status['state'] != 'ready':
        return jsonify({'error': 'Model not ready', 'status': status}), 503

    instruct = (body.get('instruct') or _book_narrator_instruct(dict(book))).strip()
    try:
        result = tts.generate_preview(
            instruct=instruct,
            sample_text=VOICE_PREVIEW_TEXT,
        )
        return jsonify({'audio_url': f'/api/audio/{result["cache_key"]}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/characters/<int:char_id>/ref-audio', methods=['POST'])
def upload_ref_audio(char_id):
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    path = os.path.join(UPLOAD_DIR, f'ref_{char_id}.wav')
    f.save(path)
    with get_conn() as conn:
        row = conn.execute('SELECT book_id FROM characters WHERE id=?', (char_id,)).fetchone()
        conn.execute('UPDATE characters SET ref_audio_path=? WHERE id=?', (path, char_id))
    if row:
        _clear_book_tts_segments(row['book_id'])
    return jsonify({'ok': True, 'path': path})


# ════════════════════════════════════════════════════════════════════════════
# TTS API
# ════════════════════════════════════════════════════════════════════════════

@app.route('/api/tts/status')
def tts_status():
    return jsonify(tts.status())


@app.route('/api/tts/load', methods=['POST'])
def tts_load():
    tts.load_async()
    return jsonify({'ok': True})


@app.route('/api/tts/generate', methods=['POST'])
def tts_generate():
    body = request.get_json(force=True)
    book_id = body.get('book_id')
    chapter_id = body.get('chapter_id')
    segment_index = body.get('segment_index', 0)

    status = tts.status()
    if status['state'] != 'ready':
        return jsonify({'error': 'Model not ready', 'status': status}), 503

    with get_conn() as conn:
        seg = conn.execute(
            'SELECT * FROM tts_segments WHERE book_id=? AND chapter_id=? AND segment_index=?',
            (book_id, chapter_id, segment_index)
        ).fetchone()

    if not seg:
        if not _build_segments_for_chapter(book_id, chapter_id):
            return jsonify({'error': 'Chapter not found'}), 404

        with get_conn() as conn:
            seg = conn.execute(
                'SELECT * FROM tts_segments WHERE book_id=? AND chapter_id=? AND segment_index=?',
                (book_id, chapter_id, segment_index)
            ).fetchone()

    if not seg:
        return jsonify({'error': 'Segment index out of range'}), 404

    seg = dict(seg)
    if seg.get('audio_path') and os.path.exists(seg['audio_path']):
        return jsonify({
            'audio_url': f'/api/audio/{seg["cache_key"]}',
            'duration_sec': seg['duration_sec'],
            'text': seg['text'],
            'character_name': seg['character_name'],
            'is_dialogue': bool(seg['is_dialogue']),
            'segment_index': segment_index,
            'cached': True,
        })

    with get_conn() as conn:
        char = None
        if seg['character_name']:
            char = conn.execute(
                'SELECT * FROM characters WHERE book_id=? AND name=?',
                (book_id, seg['character_name'])
            ).fetchone()

    ref_audio = dict(char)['ref_audio_path'] if char and char['ref_audio_path'] else None

    try:
        result = tts.generate(
            text=seg['enriched_text'],
            instruct=seg['instruct'],
            ref_audio=ref_audio,
            speed=seg['speed'],
        )
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    with get_conn() as conn:
        conn.execute(
            'UPDATE tts_segments SET audio_path=?, duration_sec=?, cache_key=? WHERE id=?',
            (result['audio_path'], result['duration_sec'], result['cache_key'], seg['id'])
        )

    return jsonify({
        'audio_url': f'/api/audio/{result["cache_key"]}',
        'duration_sec': result['duration_sec'],
        'text': seg['text'],
        'character_name': seg['character_name'],
        'is_dialogue': bool(seg['is_dialogue']),
        'segment_index': segment_index,
        'cached': result['cache_hit'],
    })


@app.route('/api/tts/segments/<int:book_id>/<int:chapter_id>')
def get_segments(book_id, chapter_id):
    """Return all segment metadata for a chapter (pre-enrich if needed)."""
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT * FROM tts_segments WHERE book_id=? AND chapter_id=? ORDER BY segment_index',
            (book_id, chapter_id)
        ).fetchall()

    if not rows:
        if not _build_segments_for_chapter(book_id, chapter_id):
            return jsonify([])
        with get_conn() as conn:
            rows = conn.execute(
                'SELECT * FROM tts_segments WHERE book_id=? AND chapter_id=? ORDER BY segment_index',
                (book_id, chapter_id)
            ).fetchall()

    return jsonify([{
        'segment_index': r['segment_index'],
        'text': r['text'],
        'character_name': r['character_name'],
        'is_dialogue': bool(r['is_dialogue']),
        'has_audio': bool(r['audio_path'] and os.path.exists(r['audio_path'])),
        'duration_sec': r['duration_sec'],
        'cache_key': r['cache_key'],
    } for r in rows])


def _store_segments(book_id, chapter_id, segs):
    with get_conn() as conn:
        conn.execute(
            'DELETE FROM tts_segments WHERE book_id=? AND chapter_id=?',
            (book_id, chapter_id)
        )
        for i, s in enumerate(segs):
            cache_key = tts.cache_key(
                s['enriched_text'], s['instruct'], None, s['speed']
            )
            conn.execute(
                'INSERT OR IGNORE INTO tts_segments '
                '(book_id, chapter_id, segment_index, text, enriched_text, '
                'character_name, instruct, speed, is_dialogue, cache_key) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)',
                (book_id, chapter_id, i, s['text'], s['enriched_text'],
                 s['character_name'], s['instruct'], s['speed'],
                 int(s['is_dialogue']), cache_key)
            )


@app.route('/api/audio/<cache_key>')
def serve_audio(cache_key):
    from core.tts_engine import AUDIO_CACHE_DIR
    path = os.path.join(AUDIO_CACHE_DIR, f'{cache_key}.wav')
    if not os.path.exists(path):
        return '', 404
    return send_file(path, mimetype='audio/wav')


# ════════════════════════════════════════════════════════════════════════════
# Export API
# ════════════════════════════════════════════════════════════════════════════

def _get_char_colors(book_id):
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT name, color_hex FROM characters WHERE book_id=?', (book_id,)
        ).fetchall()
    return {r['name']: r['color_hex'] for r in rows}


def _get_chapter_segments(chapter_id, book_id):
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT * FROM tts_segments WHERE book_id=? AND chapter_id=? ORDER BY segment_index',
            (book_id, chapter_id)
        ).fetchall()
    if not rows:
        _build_segments_for_chapter(book_id, chapter_id)
        with get_conn() as conn:
            rows = conn.execute(
                'SELECT * FROM tts_segments WHERE book_id=? AND chapter_id=? ORDER BY segment_index',
                (book_id, chapter_id)
            ).fetchall()
    return [dict(r) for r in rows]


@app.route('/api/books/<int:book_id>/export/chapter/<int:chapter_id>', methods=['POST'])
def export_chapter(book_id, chapter_id):
    body = request.get_json(force=True) or {}
    audio_fmt = body.get('audio_fmt', 'wav')
    sub_fmt = body.get('sub_fmt', 'ass')

    with get_conn() as conn:
        ch = conn.execute('SELECT * FROM chapters WHERE id=? AND book_id=?',
                          (chapter_id, book_id)).fetchone()
        book = conn.execute('SELECT title FROM books WHERE id=?', (book_id,)).fetchone()
    if not ch:
        return jsonify({'error': 'Chapter not found'}), 404

    segs = _get_chapter_segments(chapter_id, book_id)
    colors = _get_char_colors(book_id)
    result = exporter.export_single_chapter(
        ch['title'], book['title'], segs, colors, audio_fmt, sub_fmt
    )
    return jsonify({
        'audio_download': f'/api/export/download?path={result["audio_path"]}',
        'subtitle_download': f'/api/export/download?path={result["subtitle_path"]}',
        'audio_fmt': result['audio_fmt'],
        'sub_fmt': result['sub_fmt'],
    })


@app.route('/api/books/<int:book_id>/export/full', methods=['POST'])
def export_full(book_id):
    body = request.get_json(force=True) or {}
    audio_fmt = body.get('audio_fmt', 'wav')
    sub_fmt = body.get('sub_fmt', 'ass')

    with get_conn() as conn:
        book = conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone()
        chapters = conn.execute(
            'SELECT id FROM chapters WHERE book_id=? ORDER BY order_num', (book_id,)
        ).fetchall()

    all_segs = []
    for ch in chapters:
        all_segs.extend(_get_chapter_segments(ch['id'], book_id))

    colors = _get_char_colors(book_id)
    result = exporter.export_full_book(book['title'], all_segs, colors, audio_fmt, sub_fmt)
    return jsonify({
        'audio_download': f'/api/export/download?path={result["audio_path"]}',
        'subtitle_download': f'/api/export/download?path={result["subtitle_path"]}',
        'audio_fmt': result['audio_fmt'],
        'sub_fmt': result['sub_fmt'],
    })


@app.route('/api/books/<int:book_id>/export/chapterwise', methods=['POST'])
def export_chapterwise(book_id):
    body = request.get_json(force=True) or {}
    audio_fmt = body.get('audio_fmt', 'wav')
    sub_fmt = body.get('sub_fmt', 'ass')

    with get_conn() as conn:
        book = conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone()
        chapters = conn.execute(
            'SELECT id, title FROM chapters WHERE book_id=? ORDER BY order_num', (book_id,)
        ).fetchall()

    chapters_data = []
    for ch in chapters:
        segs = _get_chapter_segments(ch['id'], book_id)
        if segs:
            chapters_data.append({'chapter_title': ch['title'], 'segments': segs})

    colors = _get_char_colors(book_id)
    zip_path = exporter.export_chapter_zip(book['title'], chapters_data, colors, audio_fmt, sub_fmt)
    return jsonify({'zip_download': f'/api/export/download?path={zip_path}'})


@app.route('/api/export/download')
def export_download():
    path = request.args.get('path', '')
    exports_dir = os.path.abspath(exporter.EXPORTS_DIR)
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(exports_dir):
        return 'Forbidden', 403
    if not os.path.exists(abs_path):
        return 'Not found', 404
    return send_file(abs_path, as_attachment=True)


# ════════════════════════════════════════════════════════════════════════════
# Bookmarks API
# ════════════════════════════════════════════════════════════════════════════

@app.route('/api/books/<int:book_id>/bookmarks')
def list_bookmarks(book_id):
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT b.*, c.title as chapter_title FROM bookmarks b '
            'JOIN chapters c ON b.chapter_id = c.id '
            'WHERE b.book_id=? ORDER BY b.created_at DESC',
            (book_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/books/<int:book_id>/bookmarks', methods=['POST'])
def add_bookmark(book_id):
    body = request.get_json(force=True) or {}
    chapter_id = body.get('chapter_id')
    segment_index = body.get('segment_index', 0)
    text_excerpt = (body.get('text_excerpt', '') or '')[:200]
    label = body.get('label', '')
    if not chapter_id:
        return jsonify({'error': 'chapter_id required'}), 400
    with get_conn() as conn:
        cur = conn.execute(
            'INSERT INTO bookmarks (book_id, chapter_id, segment_index, text_excerpt, label) '
            'VALUES (?,?,?,?,?)',
            (book_id, chapter_id, segment_index, text_excerpt, label)
        )
    return jsonify({'ok': True, 'id': cur.lastrowid})


@app.route('/api/books/<int:book_id>/bookmarks/<int:bm_id>', methods=['DELETE'])
def delete_bookmark(book_id, bm_id):
    with get_conn() as conn:
        conn.execute('DELETE FROM bookmarks WHERE id=? AND book_id=?', (bm_id, book_id))
    return jsonify({'ok': True})


# ════════════════════════════════════════════════════════════════════════════
# Settings API
# ════════════════════════════════════════════════════════════════════════════

@app.route('/settings')
def settings_page():
    return render_template('settings.html')


@app.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(app_settings.load())


@app.route('/api/settings', methods=['POST'])
def save_settings():
    body = request.get_json(force=True) or {}
    previous = app_settings.load()
    allowed = {
        'model_source', 'model_path', 'model_repo', 'hf_endpoint',
        'narrator_instruct', 'single_narrator_mode', 'default_speed', 'audio_format',
        'subtitle_format', 'theme', 'font_size', 'font_family', 'line_height',
    }
    updates = {k: v for k, v in body.items() if k in allowed}
    result = app_settings.save(updates)

    # If model path changed, reset TTS so it reloads from new path
    if 'model_path' in updates:
        tts.model_path = updates['model_path']
        tts._ready = False
        tts._error = None
        tts.model = None

    if 'narrator_instruct' in updates and updates['narrator_instruct'] != previous.get('narrator_instruct'):
        with get_conn() as conn:
            conn.execute(
                'DELETE FROM tts_segments WHERE book_id IN (SELECT id FROM books WHERE narrator_instruct IS NULL)'
            )

    return jsonify({'ok': True, 'settings': result})


@app.route('/api/settings/spacy-status')
def spacy_status_route():
    status = app_settings.spacy_status()
    status['error'] = char_module.spacy_error()
    return jsonify(status)


@app.route('/api/settings/spacy-install', methods=['POST'])
def spacy_install():
    result = app_settings.install_spacy_model()
    if result['ok']:
        # Reset spaCy NLP so it reloads the new model
        import core.characters as cm
        cm._nlp = None
        cm._spacy_error = ''
    return jsonify(result)


@app.route('/api/settings/model-download', methods=['POST'])
def start_download():
    body = request.get_json(force=True) or {}
    repo_id = body.get('repo_id', app_settings.get('model_repo', 'k2-fsa/OmniVoice'))
    dest = body.get('dest', app_settings.get('model_path'))
    hf_endpoint = body.get('hf_endpoint', app_settings.get('hf_endpoint', ''))
    app_settings.start_model_download(repo_id, dest, hf_endpoint)
    return jsonify({'ok': True, 'dest': dest})


@app.route('/api/settings/model-download/progress')
def download_progress():
    return jsonify(app_settings.download_state())


@app.route('/api/settings/tts-reload', methods=['POST'])
def tts_reload():
    tts.reload()
    return jsonify({'ok': True})


@app.route('/api/settings/check-model-path', methods=['POST'])
def check_model_path():
    body = request.get_json(force=True) or {}
    path = body.get('path', '')
    exists = os.path.isdir(path)
    has_config = os.path.exists(os.path.join(path, 'config.json'))
    return jsonify({'exists': exists, 'has_config': has_config, 'path': path})


# ════════════════════════════════════════════════════════════════════════════
# Run
# ════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=7860, debug=False, threaded=True)
