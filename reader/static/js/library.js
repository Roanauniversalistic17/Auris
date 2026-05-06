async function loadBooks() {
  const grid = document.getElementById('book-grid');
  const books = await fetch('/api/books').then(r => r.json());

  const countEl = document.getElementById('library-count');
  if (countEl) countEl.textContent = books.length
    ? books.length + (books.length === 1 ? ' title' : ' titles')
    : '';

  if (!books.length) {
    grid.innerHTML = `
      <div class="empty-library">
        <p>Your library is empty.</p>
        <p class="sub">Import an EPUB, PDF, or TXT file to get started.</p>
      </div>`;
    return;
  }

  grid.innerHTML = books.map(b => {
    const coverHtml = b.cover_url
      ? `<img src="${b.cover_url}" alt="" loading="lazy">`
      : `<div class="book-cover-placeholder">${esc(b.title)}</div>`;

    return `
    <div class="book-card" data-id="${b.id}">
      <div class="book-cover">${coverHtml}</div>
      <span class="book-type-badge">${esc(b.file_type)}</span>
      <div class="book-info">
        <div class="book-title">${esc(b.title)}</div>
        <div class="book-author">${esc(b.author || 'Unknown')}</div>
        <div class="book-author" style="margin-top:3px;font-size:.68rem">
          ${b.total_chapters} section${b.total_chapters !== 1 ? 's' : ''}
        </div>
      </div>
      <div class="book-actions">
        <a href="/reader/${b.id}">Read</a>
        <button class="del-btn" onclick="deleteBook(event,${b.id})">Remove</button>
      </div>
    </div>`;
  }).join('');
}

async function deleteBook(e, id) {
  e.stopPropagation();
  if (!confirm('Remove this book from the library?')) return;
  await fetch(`/api/books/${id}`, { method: 'DELETE' });
  loadBooks();
}

document.getElementById('file-input').addEventListener('change', async function() {
  const file = this.files[0];
  if (!file) return;
  const status = document.getElementById('import-status');
  status.textContent = `Importing “${file.name}”…`;
  status.className = 'import-status';
  status.classList.remove('hidden');
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/api/books/import', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    status.textContent = `“${d.title}” imported — ${d.chapters} sections. Detecting characters in background…`;
    loadBooks();
  } catch(e) {
    status.textContent = e.message;
    status.className = 'import-status error';
  }
  this.value = '';
});

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

loadBooks();
