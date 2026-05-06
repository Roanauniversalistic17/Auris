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
let _progressSaveTimer = null;
let _scrollProgressTimer = null;
let _lastSavedProgressKey = '';
let _ignoreScrollTrackingUntil = 0;

// Two audio elements for gapless double-buffering
const _audioA = document.getElementById('tts-audio');
const _audioB = (() => { const a = new Audio(); a.preload = 'auto'; return a; })();
let audio = _audioA; // currently active (playing) element

// Client-side cache: segIdx -> Promise<{audio_url, duration_sec, cache_key, text}>
let _segCache = new Map();

// Which segment index is pre-buffered in the standby element, and its data
let _preloadIdx = -1;
let _preloadData = null;

function _standby() { return audio === _audioA ? _audioB : _audioA; }
function _swapAudio() { audio = (audio === _audioA ? _audioB : _audioA); }

function clampSegmentIndex(idx, segList = segments) {
  const parsed = Number.parseInt(idx, 10);
  const safe = Number.isFinite(parsed) ? parsed : 0;
  const max = Math.max((segList?.length || 1) - 1, 0);
  return Math.max(0, Math.min(safe, max));
}

function progressKey(chapterId, position) {
  return `${chapterId}:${position}`;
}

function sendProgress(chapterId, position, options = {}) {
  const { useBeacon = false, force = false } = options;
  if (!chapterId) return;

  const clamped = clampSegmentIndex(position);
  const key = progressKey(chapterId, clamped);
  if (!force && key === _lastSavedProgressKey) return;
  _lastSavedProgressKey = key;

  const payload = JSON.stringify({ chapter_id: chapterId, position: clamped });
  const url = `/api/books/${BOOK_ID}/progress`;

  if (useBeacon && navigator.sendBeacon) {
    const ok = navigator.sendBeacon(url, new Blob([payload], { type: 'application/json' }));
    if (ok) return;
  }

  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload,
    keepalive: useBeacon,
  }).catch(() => {});
}

function queueProgressSave(chapterId = currentChapterId, position = currentSegIdx) {
  if (!chapterId) return;
  clearTimeout(_progressSaveTimer);
  _progressSaveTimer = setTimeout(() => {
    sendProgress(chapterId, position);
  }, 250);
}

function flushProgressSave(options = {}) {
  clearTimeout(_progressSaveTimer);
  if (currentChapterId) {
    sendProgress(currentChapterId, currentSegIdx, options);
  }
}

function setCurrentSegment(idx, options = {}) {
  const { highlight = false, behavior = 'smooth', save = true } = options;
  currentSegIdx = clampSegmentIndex(idx);
  if (highlight) {
    highlightSegment(currentSegIdx, { behavior });
  }
  updatePlaybackUI();
  updateProgress();
  if (save) {
    queueProgressSave(currentChapterId, currentSegIdx);
  }
  return currentSegIdx;
}

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
  const savedChapterId = Number.parseInt(prog.chapter_id, 10);
  const savedPosition = Number.parseInt(prog.position, 10);
  const hasSavedChapter = chapters.some(ch => ch.id === savedChapterId);
  if (hasSavedChapter) {
    openChapter(savedChapterId, {
      resumePosition: savedPosition,
      persistOpened: false,
      highlightOnLoad: true,
    });
  } else if (chapters.length) {
    openChapter(chapters[0].id, {
      resumePosition: 0,
      persistOpened: false,
      highlightOnLoad: false,
    });
  }

  loadBookmarks();
}

async function openChapter(chapterId, options = {}) {
  const {
    resumePosition = 0,
    persistCurrent = true,
    persistOpened = true,
    highlightOnLoad = false,
  } = options;

  if (persistCurrent && currentChapterId && currentChapterId !== chapterId) {
    sendProgress(currentChapterId, currentSegIdx, { force: true });
  }

  stopPlayback();
  _segCache = new Map();
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

  document.getElementById('chapter-content').scrollTop = 0;

  const startIdx = clampSegmentIndex(resumePosition);
  setCurrentSegment(startIdx, {
    highlight: highlightOnLoad,
    behavior: 'auto',
    save: persistOpened,
  });
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
  if (isPlaying) playSegment(idx);
  else { setCurrentSegment(idx, { highlight: true, save: true }); }
}

// ── Playback ──────────────────────────────────────────────────────────────────

function fetchSegmentData(idx) {
  if (!_segCache.has(idx)) {
    _segCache.set(idx, fetch('/api/tts/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ book_id: BOOK_ID, chapter_id: currentChapterId, segment_index: idx }),
    }).then(r => {
      if (!r.ok) return r.json().then(e => { throw new Error(e.error || `HTTP ${r.status}`); });
      return r.json();
    }));
  }
  return _segCache.get(idx);
}

async function _schedulePreload(playingIdx) {
  const nextIdx = playingIdx + 1;
  if (nextIdx >= segments.length || !isPlaying) return;

  // Fire off generate requests for 2–4 ahead in parallel to warm the server cache
  for (let i = 2; i <= 4; i++) {
    const ahead = playingIdx + i;
    if (ahead < segments.length) fetchSegmentData(ahead);
  }

  try {
    const data = await fetchSegmentData(nextIdx);
    if (!isPlaying || _preloadIdx === nextIdx) return;

    const sb = _standby();
    sb.src = data.audio_url;
    sb.playbackRate = speedMultiplier;
    _preloadIdx = nextIdx;
    _preloadData = data;
  } catch (e) {
    // silent — playback will continue without the preload, just with a small gap
  }
}

async function playSegment(idx) {
  if (idx >= segments.length) { stopPlayback(); return; }

  isPlaying = true;
  setCurrentSegment(idx, { highlight: true, save: true });

  const seg = segments[idx];
  const charEl = document.getElementById('pb-character');
  charEl.textContent = seg.character_name || 'Narrator';

  try {
    let data;

    if (_preloadIdx === idx && _preloadData) {
      // Standby element is already buffered with this segment — swap and play instantly
      _swapAudio();
      data = _preloadData;
      _preloadIdx = -1;
      _preloadData = null;
    } else {
      data = await fetchSegmentData(idx);
      if (!isPlaying) return;
      audio.src = data.audio_url;
    }

    audio.playbackRate = speedMultiplier;
    startWordHighlight(idx, data.duration_sec);
    await audio.play();

    _schedulePreload(idx);

  } catch(e) {
    charEl.textContent = e.message;
    stopPlayback();
  }
}

function _onAudioEnded() {
  stopWordHighlight();
  if (!isPlaying) return;
  const next = currentSegIdx + 1;
  if (next < segments.length) playSegment(next);
  else {
    queueProgressSave(currentChapterId, currentSegIdx);
    stopPlayback();
  }
}
_audioA.addEventListener('ended', _onAudioEnded);
_audioB.addEventListener('ended', _onAudioEnded);

function stopPlayback() {
  isPlaying = false;
  stopWordHighlight();
  _audioA.pause();
  _audioB.pause();
  _audioA.src = '';
  _audioB.src = '';
  audio = _audioA; // reset active to primary
  _preloadIdx = -1;
  _preloadData = null;
  if (currentChapterId) queueProgressSave(currentChapterId, currentSegIdx);
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

function highlightSegment(idx, options = {}) {
  const behavior = options.behavior || 'smooth';
  _ignoreScrollTrackingUntil = Date.now() + (behavior === 'smooth' ? 700 : 150);
  document.querySelectorAll('.sentence').forEach((el, i) => {
    el.classList.toggle('playing', i === idx);
    el.classList.toggle('spoken',  i < idx);
  });
  const active = document.querySelector(`.sentence[data-idx="${idx}"]`);
  if (active) active.scrollIntoView({ behavior, block: 'center' });
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
    queueProgressSave(currentChapterId, currentSegIdx);
  } else {
    playSegment(currentSegIdx);
  }
};

document.getElementById('btn-stop').onclick  = stopPlayback;

document.getElementById('btn-next-seg').onclick = () => {
  const next = Math.min(currentSegIdx + 1, segments.length - 1);
  if (isPlaying) playSegment(next);
  else { setCurrentSegment(next, { highlight: true, save: true }); }
};

document.getElementById('btn-prev-seg').onclick = () => {
  const prev = Math.max(currentSegIdx - 1, 0);
  if (isPlaying) playSegment(prev);
  else { setCurrentSegment(prev, { highlight: true, save: true }); }
};

document.getElementById('speed-slider').oninput = function() {
  speedMultiplier = parseFloat(this.value);
  document.getElementById('speed-val').textContent = speedMultiplier.toFixed(1) + '×';
  _audioA.playbackRate = speedMultiplier;
  _audioB.playbackRate = speedMultiplier;
};

// ── Sidebar toggle ────────────────────────────────────────────────────────────

document.getElementById('toc-toggle').onclick = () => {
  document.getElementById('toc-sidebar').classList.toggle('collapsed');
};

function toggleBookmarkPanel() {
  document.getElementById('bookmarks-panel').classList.toggle('collapsed');
}

// ── Progress persistence ──────────────────────────────────────────────────────

function updateProgressFromViewport() {
  if (!segments.length || isPlaying || Date.now() < _ignoreScrollTrackingUntil) return;

  const sentenceEls = document.querySelectorAll('.sentence');
  if (!sentenceEls.length) return;

  const viewportCenter = window.innerHeight * 0.35;
  let bestIdx = currentSegIdx;
  let bestDistance = Number.POSITIVE_INFINITY;

  sentenceEls.forEach((el, idx) => {
    const rect = el.getBoundingClientRect();
    const mid = rect.top + (rect.height / 2);
    const distance = Math.abs(mid - viewportCenter);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIdx = idx;
    }
  });

  if (bestIdx !== currentSegIdx) {
    currentSegIdx = bestIdx;
    updatePlaybackUI();
    updateProgress();
    queueProgressSave(currentChapterId, currentSegIdx);
  }
}

function scheduleViewportProgressUpdate() {
  if (!segments.length || isPlaying || Date.now() < _ignoreScrollTrackingUntil) return;
  clearTimeout(_scrollProgressTimer);
  _scrollProgressTimer = setTimeout(updateProgressFromViewport, 120);
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
    openChapter(chapterId, {
      resumePosition: segIdx,
      persistCurrent: true,
      persistOpened: true,
      highlightOnLoad: true,
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
  if (!currentChapterId) { showToast('Open a chapter first.'); return; }

  const mode      = document.querySelector('input[name="exp-mode"]:checked').value;
  const audioFmt  = document.querySelector('input[name="exp-audio"]:checked').value;
  const subInput  = document.querySelector('input[name="exp-sub"]:checked');
  const subFmt    = subInput ? subInput.value : 'srt';

  const status    = document.getElementById('export-status');
  const progWrap  = document.getElementById('export-progress-wrap');
  const progFill  = document.getElementById('export-progress-fill');
  const doBtn     = document.getElementById('do-export-btn');

  doBtn.disabled = true;
  progWrap.classList.add('active');
  progFill.style.width = '0%';
  status.textContent = 'Starting export…';

  let url;
  if (mode === 'chapter')          url = `/api/books/${BOOK_ID}/export/chapter/${currentChapterId}`;
  else if (mode === 'chapterwise') url = `/api/books/${BOOK_ID}/export/chapterwise`;
  else                             url = `/api/books/${BOOK_ID}/export/full`;

  const finish = (msg) => {
    status.textContent = msg;
    setTimeout(() => {
      progWrap.classList.remove('active');
      progFill.style.width = '0%';
      doBtn.disabled = false;
    }, 2000);
  };

  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ audio_fmt: audioFmt, sub_fmt: subFmt }),
    });
    const d = await r.json();
    if (d.error) { finish(d.error); return; }

    const jobId = d.job_id;

    while (true) {
      await new Promise(res => setTimeout(res, 800));
      let sr;
      try { sr = await fetch(`/api/export/status/${jobId}`).then(r => r.json()); }
      catch(_) { continue; }

      if (sr.total > 0) {
        const pct = Math.min(Math.round((sr.done / sr.total) * 95), 95);
        progFill.style.width = pct + '%';
      }
      status.textContent = sr.message || 'Working…';

      if (sr.state === 'complete') {
        progFill.style.width = '100%';
        const res = sr.result;
        if (res.zip_download) {
          window.location.href = res.zip_download;
        } else {
          if (res.audio_download)    window.open(res.audio_download);
          if (res.subtitle_download) setTimeout(() => window.open(res.subtitle_download), 500);
        }
        finish('Done. Downloading…');
        break;
      } else if (sr.state === 'failed') {
        finish('Export failed: ' + (sr.error || 'Unknown error'));
        break;
      }
    }
  } catch(e) {
    finish(e.message);
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

window.addEventListener('scroll', scheduleViewportProgressUpdate, { passive: true });
window.addEventListener('pagehide', () => flushProgressSave({ useBeacon: true, force: true }));
window.addEventListener('beforeunload', () => flushProgressSave({ useBeacon: true, force: true }));
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    flushProgressSave({ useBeacon: true, force: true });
  }
});

loadTOC();
