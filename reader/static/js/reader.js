const BOOK_ID = window.BOOK_ID;

// ── State ─────────────────────────────────────────────────────────────────────
let chapters        = [];
let currentChapterId= null;
let segments        = [];
let currentSegIdx   = 0;
let isPlaying       = false;
let speedMultiplier = 1.0;
let fontSize        = parseInt(localStorage.getItem('fontSize') || '18');
let fontFamily      = localStorage.getItem('fontFamily') || 'serif';
let lineHeight      = parseFloat(localStorage.getItem('lineHeight') || '1.9');
let currentTheme    = localStorage.getItem('theme') || 'night';

const audio = document.getElementById('tts-audio');

// ── Theme ─────────────────────────────────────────────────────────────────────

const THEMES = ['night', 'sepia', 'paper', 'amoled'];

function applyTheme(theme) {
  THEMES.forEach(t => document.body.classList.remove('theme-' + t));
  if (theme !== 'night') document.body.classList.add('theme-' + theme);
  currentTheme = theme;
  localStorage.setItem('theme', theme);
}

function cycleTheme() {
  const next = THEMES[(THEMES.indexOf(currentTheme) + 1) % THEMES.length];
  applyTheme(next);
  showToast('Theme: ' + next.charAt(0).toUpperCase() + next.slice(1));
}

applyTheme(currentTheme);

// ── Font settings ─────────────────────────────────────────────────────────────

const FONT_FAMILIES = {
  serif: "Georgia, 'Palatino Linotype', serif",
  sans:  "'Helvetica Neue', Arial, sans-serif",
  mono:  "'Courier New', Courier, monospace",
};

function applyFontFamily(ff) {
  fontFamily = ff;
  localStorage.setItem('fontFamily', ff);
  document.getElementById('chapter-content').style.fontFamily = FONT_FAMILIES[ff] || FONT_FAMILIES.serif;
}

function changeFontSize(delta) {
  fontSize = Math.min(30, Math.max(13, fontSize + delta));
  document.getElementById('chapter-content').style.fontSize = fontSize + 'px';
  localStorage.setItem('fontSize', fontSize);
}

function applyLineHeight(lh) {
  lineHeight = lh;
  localStorage.setItem('lineHeight', lh);
  document.getElementById('chapter-content').style.lineHeight = lh;
}

// Apply saved reading preferences
(function initReadingPrefs() {
  const cc = document.getElementById('chapter-content');
  if (!cc) return;
  cc.style.fontSize    = fontSize + 'px';
  cc.style.lineHeight  = lineHeight;
  cc.style.fontFamily  = FONT_FAMILIES[fontFamily] || FONT_FAMILIES.serif;
})();

// ── TOC ───────────────────────────────────────────────────────────────────────

async function loadTOC() {
  chapters = await fetch(`/api/books/${BOOK_ID}/chapters`).then(r => r.json());
  const list = document.getElementById('toc-list');
  list.innerHTML = chapters.map(ch => {
    const wc = ch.word_count ? ch.word_count.toLocaleString() + ' words' : '';
    const badge = ch.section_type !== 'chapter'
      ? `<span class="toc-section-badge">${esc(ch.section_type)}</span>` : '';
    return `
      <div class="toc-item" data-id="${ch.id}" onclick="openChapter(${ch.id})">
        ${badge}
        <span class="toc-item-title">${esc(ch.title)}</span>
        ${wc ? `<span class="toc-item-meta">${wc}</span>` : ''}
      </div>`;
  }).join('');

  const prog = await fetch(`/api/books/${BOOK_ID}/progress`).then(r => r.json());
  if (prog.chapter_id) openChapter(prog.chapter_id);
  else if (chapters.length) openChapter(chapters[0].id);

  loadBookmarks();
}

async function openChapter(chapterId) {
  stopPlayback();
  currentChapterId = chapterId;
  currentSegIdx    = 0;

  document.querySelectorAll('.toc-item').forEach(el => {
    el.classList.toggle('active', +el.dataset.id === chapterId);
  });

  const ch = await fetch(`/api/books/${BOOK_ID}/chapters/${chapterId}`).then(r => r.json());
  document.getElementById('chapter-title').textContent = ch.title;

  const wpm = 250;
  const minutes = Math.round((ch.word_count || 0) / wpm);
  const estEl = document.getElementById('reading-estimate');
  if (estEl) estEl.textContent = minutes > 0 ? `~${minutes} min read` : '';

  segments = await fetch(`/api/tts/segments/${BOOK_ID}/${chapterId}`).then(r => r.json());
  renderContent(segments);
  updatePlaybackUI();
  updateProgress();
  saveProgress(chapterId, 0);
}

// ── Content rendering ─────────────────────────────────────────────────────────

function renderContent(segs) {
  const container = document.getElementById('chapter-content');

  if (!segs || !segs.length) {
    container.innerHTML = '<div class="placeholder-text">No content available.</div>';
    return;
  }

  const html = segs.map((seg, i) => {
    const words = seg.text.split(/(\s+)/);
    const wordSpans = words.map((w, wi) => {
      if (/^\s+$/.test(w)) return w;
      return `<span class="word" data-seg="${i}" data-word="${wi}">${esc(w)}</span>`;
    }).join('');

    const charAttr = seg.character_name ? ` data-char="${esc(seg.character_name)}"` : '';
    const cls = 'sentence' + (seg.is_dialogue ? ' dialogue-sent' : '');
    return `<span class="${cls}" data-idx="${i}"${charAttr} onclick="jumpTo(${i})">${wordSpans}</span> `;
  }).join('');

  container.innerHTML = `<div>${html}</div>`;

  // Restore font prefs (font may be reset by innerHTML)
  container.style.fontSize   = fontSize + 'px';
  container.style.lineHeight = lineHeight;
  container.style.fontFamily = FONT_FAMILIES[fontFamily] || FONT_FAMILIES.serif;
}

function jumpTo(idx) {
  currentSegIdx = idx;
  if (isPlaying) playSegment(idx);
  else { highlightSegment(idx); updateProgress(); }
}

// ── Playback ──────────────────────────────────────────────────────────────────

async function playSegment(idx) {
  if (idx >= segments.length) { stopPlayback(); return; }

  isPlaying      = true;
  currentSegIdx  = idx;
  highlightSegment(idx);
  updatePlaybackUI();
  updateProgress();

  const seg = segments[idx];
  const charEl = document.getElementById('pb-character');
  charEl.textContent = seg.character_name || 'Narrator';

  try {
    const r = await fetch('/api/tts/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ book_id: BOOK_ID, chapter_id: currentChapterId, segment_index: idx }),
    });

    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      charEl.textContent = err.error || 'TTS error';
      stopPlayback();
      return;
    }

    const data = await r.json();

    // Pre-fetch next segment
    if (idx + 1 < segments.length) prefetchSegment(idx + 1);

    audio.src          = data.audio_url + '?t=' + Date.now();
    audio.playbackRate = speedMultiplier;
    startWordHighlight(idx, data.duration_sec);
    await audio.play();

  } catch(e) {
    document.getElementById('pb-character').textContent = e.message;
    stopPlayback();
  }
}

function prefetchSegment(idx) {
  if (idx >= segments.length) return;
  fetch('/api/tts/generate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ book_id: BOOK_ID, chapter_id: currentChapterId, segment_index: idx }),
  }).catch(() => {});
}

audio.addEventListener('ended', () => {
  stopWordHighlight();
  if (!isPlaying) return;
  const next = currentSegIdx + 1;
  if (next < segments.length) playSegment(next);
  else stopPlayback();
});

function stopPlayback() {
  isPlaying = false;
  stopWordHighlight();
  audio.pause();
  audio.src = '';
  const btn = document.getElementById('btn-play');
  btn.innerHTML = '&#9654;';
  btn.classList.add('paused');
  document.getElementById('pb-character').textContent = '—';
  updatePlaybackUI();
}

// ── Word-level highlighting ───────────────────────────────────────────────────

let _wordRafId = null;

function startWordHighlight(segIdx, durationSec) {
  stopWordHighlight();
  if (!durationSec) return;

  const wordEls = document.querySelectorAll(`.word[data-seg="${segIdx}"]`);
  if (!wordEls.length) return;

  const n      = wordEls.length;
  const start  = audio.currentTime;

  function tick() {
    const elapsed   = (audio.currentTime - start) * speedMultiplier;
    const wordIdx   = Math.min(Math.floor((elapsed / durationSec) * n), n - 1);
    wordEls.forEach((el, i) => el.classList.toggle('playing', i === wordIdx));
    if (isPlaying && !audio.paused) _wordRafId = requestAnimationFrame(tick);
  }

  _wordRafId = requestAnimationFrame(tick);
}

function stopWordHighlight() {
  if (_wordRafId) { cancelAnimationFrame(_wordRafId); _wordRafId = null; }
  document.querySelectorAll('.word.playing').forEach(el => el.classList.remove('playing'));
}

// ── Segment highlighting & auto-scroll ────────────────────────────────────────

function highlightSegment(idx) {
  document.querySelectorAll('.sentence').forEach((el, i) => {
    el.classList.toggle('playing', i === idx);
    el.classList.toggle('spoken',  i < idx);
  });
  const active = document.querySelector(`.sentence[data-idx="${idx}"]`);
  if (active) active.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ── Progress bar ──────────────────────────────────────────────────────────────

function updateProgress() {
  if (!segments.length) return;
  const pct = ((currentSegIdx) / segments.length) * 100;
  const fill = document.getElementById('chapter-progress-fill');
  if (fill) fill.style.width = pct + '%';
}

// ── Playback UI ───────────────────────────────────────────────────────────────

function updatePlaybackUI() {
  const btn  = document.getElementById('btn-play');
  const prog = document.getElementById('pb-progress');
  if (isPlaying) {
    btn.innerHTML = '&#9646;&#9646;';
    btn.classList.remove('paused');
  } else {
    btn.innerHTML = '&#9654;';
    btn.classList.add('paused');
  }
  if (segments.length) prog.textContent = `${currentSegIdx + 1} / ${segments.length}`;
}

// ── Controls ──────────────────────────────────────────────────────────────────

document.getElementById('btn-play').onclick = () => {
  if (isPlaying) {
    isPlaying = false;
    stopWordHighlight();
    audio.pause();
    updatePlaybackUI();
  } else {
    playSegment(currentSegIdx);
  }
};

document.getElementById('btn-stop').onclick  = stopPlayback;

document.getElementById('btn-next-seg').onclick = () => {
  const next = Math.min(currentSegIdx + 1, segments.length - 1);
  if (isPlaying) playSegment(next);
  else { currentSegIdx = next; highlightSegment(next); updatePlaybackUI(); updateProgress(); }
};

document.getElementById('btn-prev-seg').onclick = () => {
  const prev = Math.max(currentSegIdx - 1, 0);
  if (isPlaying) playSegment(prev);
  else { currentSegIdx = prev; highlightSegment(prev); updatePlaybackUI(); updateProgress(); }
};

document.getElementById('speed-slider').oninput = function() {
  speedMultiplier = parseFloat(this.value);
  document.getElementById('speed-val').textContent = speedMultiplier.toFixed(1) + '×';
  audio.playbackRate = speedMultiplier;
};

// ── Sidebar toggle ────────────────────────────────────────────────────────────

document.getElementById('toc-toggle').onclick = () => {
  document.getElementById('toc-sidebar').classList.toggle('collapsed');
};

function toggleBookmarkPanel() {
  document.getElementById('bookmarks-panel').classList.toggle('collapsed');
}

// ── Progress persistence ──────────────────────────────────────────────────────

function saveProgress(chapterId, position) {
  fetch(`/api/books/${BOOK_ID}/progress`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ chapter_id: chapterId, position }),
  }).catch(() => {});
}

// ── Bookmarks ─────────────────────────────────────────────────────────────────

let _bookmarks = [];

async function loadBookmarks() {
  _bookmarks = await fetch(`/api/books/${BOOK_ID}/bookmarks`).then(r => r.json());
  renderBookmarks();
}

function renderBookmarks() {
  const list = document.getElementById('bookmark-list');
  if (!_bookmarks.length) {
    list.innerHTML = '<div style="padding:16px;font-size:.8rem;color:var(--text3);font-style:italic">No bookmarks yet.</div>';
    return;
  }
  list.innerHTML = _bookmarks.map(bm => `
    <div class="bookmark-item" onclick="gotoBookmark(${bm.chapter_id}, ${bm.segment_index})">
      <div class="bookmark-text">${esc(bm.text_excerpt || bm.label || '(no excerpt)')}</div>
      <div class="bookmark-loc">${esc(bm.chapter_title || '')} &middot; seg ${bm.segment_index + 1}</div>
      <button class="bookmark-del" onclick="removeBookmark(event,${bm.id})">&times;</button>
    </div>`).join('');
}

async function addBookmark() {
  if (!currentChapterId) { showToast('Open a chapter first.'); return; }
  const seg = segments[currentSegIdx];
  const excerpt = seg ? seg.text.slice(0, 120) : '';
  const r = await fetch(`/api/books/${BOOK_ID}/bookmarks`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      chapter_id:    currentChapterId,
      segment_index: currentSegIdx,
      text_excerpt:  excerpt,
    }),
  });
  if (r.ok) {
    showToast('Bookmark added');
    loadBookmarks();
    const btn = document.getElementById('bookmark-btn');
    btn.textContent = '★';
    setTimeout(() => { btn.textContent = '☆'; }, 1500);
  }
}

async function removeBookmark(e, id) {
  e.stopPropagation();
  await fetch(`/api/books/${BOOK_ID}/bookmarks/${id}`, { method: 'DELETE' });
  loadBookmarks();
}

function gotoBookmark(chapterId, segIdx) {
  if (chapterId !== currentChapterId) {
    openChapter(chapterId).then(() => {
      currentSegIdx = segIdx;
      highlightSegment(segIdx);
      updateProgress();
    });
  } else {
    jumpTo(segIdx);
  }
}

// ── Export ────────────────────────────────────────────────────────────────────

document.getElementById('export-btn').onclick = () => {
  document.getElementById('export-dropdown').classList.toggle('hidden');
};

document.getElementById('do-export-btn').onclick = async () => {
  const mode     = document.querySelector('input[name="exp-mode"]:checked').value;
  const audioFmt = document.querySelector('input[name="exp-audio"]:checked').value;
  const subFmt   = document.querySelector('input[name="exp-sub"]:checked').value;
  const status   = document.getElementById('export-status');
  status.textContent = 'Generating… this may take a while.';

  const body = { audio_fmt: audioFmt, sub_fmt: subFmt };
  let url;
  if (mode === 'chapter')      url = `/api/books/${BOOK_ID}/export/chapter/${currentChapterId}`;
  else if (mode === 'chapterwise') url = `/api/books/${BOOK_ID}/export/chapterwise`;
  else                         url = `/api/books/${BOOK_ID}/export/full`;

  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.error) { status.textContent = d.error; return; }
    status.textContent = 'Ready. Downloading…';
    if (d.zip_download) {
      window.location.href = d.zip_download;
    } else {
      if (d.audio_download)    window.open(d.audio_download);
      if (d.subtitle_download) setTimeout(() => window.open(d.subtitle_download), 500);
    }
  } catch(e) {
    status.textContent = e.message;
  }
};

// ── Keyboard shortcuts ────────────────────────────────────────────────────────

document.addEventListener('keydown', e => {
  const tag = document.activeElement.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

  switch(e.key) {
    case ' ':
      e.preventDefault();
      document.getElementById('btn-play').click();
      break;
    case 'ArrowLeft':
      e.preventDefault();
      document.getElementById('btn-prev-seg').click();
      break;
    case 'ArrowRight':
      e.preventDefault();
      document.getElementById('btn-next-seg').click();
      break;
    case 'b': case 'B':
      addBookmark();
      break;
    case 't': case 'T':
      cycleTheme();
      break;
    case 'c': case 'C':
      document.getElementById('toc-toggle').click();
      break;
    case 'm': case 'M':
      toggleBookmarkPanel();
      break;
    case '?':
      showShortcuts();
      break;
    case 'Escape':
      hideShortcuts();
      document.getElementById('export-dropdown').classList.add('hidden');
      break;
  }
});

function showShortcuts()  { document.getElementById('shortcuts-overlay').classList.remove('hidden'); }
function hideShortcuts()  { document.getElementById('shortcuts-overlay').classList.add('hidden'); }

// ── Toasts ────────────────────────────────────────────────────────────────────

function showToast(msg, type = 'ok') {
  const tc   = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = msg;
  tc.appendChild(toast);
  setTimeout(() => toast.remove(), 2500);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Init ──────────────────────────────────────────────────────────────────────

loadTOC();
