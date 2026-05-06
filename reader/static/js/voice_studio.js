const BOOK_ID = window.BOOK_ID;
const NARRATOR_INSTRUCT = window.NARRATOR_INSTRUCT || "";
const previewAudio = document.getElementById("preview-audio");

const GENDERS = ["male", "female"];
const AGES = ["child", "teenager", "young adult", "middle-aged", "elderly"];
const PITCHES = ["very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"];
const ACCENTS = [
  "american accent",
  "british accent",
  "australian accent",
  "canadian accent",
  "indian accent",
  "chinese accent",
  "korean accent",
  "japanese accent",
];

function buildSelect(options, selected, id) {
  return `<select class="vc-select" id="${id}">
    ${options
      .map((o) => `<option value="${o}"${o === selected ? " selected" : ""}>${o}</option>`)
      .join("")}
  </select>`;
}

function parseInstruct(instruct) {
  const parts = String(instruct || "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  return {
    gender: parts.find((p) => GENDERS.includes(p)) || "female",
    age: AGES.find((a) => parts.includes(a)) || "young adult",
    pitch: PITCHES.find((p) => parts.includes(p)) || "moderate pitch",
    accent: ACCENTS.find((a) => parts.includes(a)) || "american accent",
  };
}

function buildInstruct(gender, age, pitch, accent) {
  return [gender, age, pitch, accent].join(", ");
}

function esc(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function flashSaved(el) {
  if (!el) return;
  const prev = el.style.color;
  el.style.color = "#4caf80";
  setTimeout(() => {
    el.style.color = prev;
  }, 1500);
}

function updateInstructPreview(charId) {
  const instruct = buildInstruct(
    document.getElementById(`g-${charId}`)?.value || "female",
    document.getElementById(`a-${charId}`)?.value || "young adult",
    document.getElementById(`p-${charId}`)?.value || "moderate pitch",
    document.getElementById(`ac-${charId}`)?.value || "american accent"
  );
  const el = document.getElementById(`ins-${charId}`);
  if (el) el.textContent = instruct;
  return instruct;
}

function getNarratorInstruct() {
  return buildInstruct(
    document.getElementById("narrator-gender")?.value || "female",
    document.getElementById("narrator-age")?.value || "middle-aged",
    document.getElementById("narrator-pitch")?.value || "moderate pitch",
    document.getElementById("narrator-accent")?.value || "american accent"
  );
}

function updateNarratorPreview() {
  const instruct = getNarratorInstruct();
  const el = document.getElementById("narrator-instruct-preview");
  if (el) el.textContent = instruct;
  return instruct;
}

function initNarratorControls() {
  const parsed = parseInstruct(NARRATOR_INSTRUCT);
  const pairs = [
    ["narrator-gender", parsed.gender],
    ["narrator-age", parsed.age],
    ["narrator-pitch", parsed.pitch],
    ["narrator-accent", parsed.accent],
  ];

  pairs.forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = value;
    el.addEventListener("change", updateNarratorPreview);
  });

  updateNarratorPreview();
}

async function loadCharacters() {
  const chars = await fetch(`/api/books/${BOOK_ID}/characters`).then((r) => r.json());
  const list = document.getElementById("char-list");
  document.getElementById("char-count").textContent = `(${chars.length} detected)`;

  if (!chars.length) {
    list.innerHTML =
      '<div class="muted" style="padding:16px">No characters detected yet. Characters are detected in the background after import. Please wait or refresh.</div>';
    return;
  }

  list.innerHTML = chars
    .map((ch) => {
      const v = parseInstruct(ch.instruct);
      const avatarStyle = `background:${ch.color_hex};color:#1a1a2e`;
      const initial = ch.name.charAt(0).toUpperCase();
      const genderBadge = `<span class="char-gender gender-${ch.gender}">${ch.gender}</span>`;
      return `
      <div class="character-card" id="card-${ch.id}">
        <div class="char-avatar" style="${avatarStyle}">${initial}</div>
        <div class="char-details">
          <span class="char-name">${esc(ch.name)} ${genderBadge} <span class="char-freq">x ${ch.frequency}</span></span>
          <div class="voice-controls">
            ${buildSelect(GENDERS, v.gender, `g-${ch.id}`)}
            ${buildSelect(AGES, v.age, `a-${ch.id}`)}
            ${buildSelect(PITCHES, v.pitch, `p-${ch.id}`)}
            ${buildSelect(ACCENTS, v.accent, `ac-${ch.id}`)}
          </div>
          <div class="char-card-footer">
            <span class="instruct-preview" id="ins-${ch.id}">${esc(ch.instruct)}</span>
            <button class="btn btn-sm btn-ghost preview-btn" onclick="previewChar(${ch.id})">&#9654; Preview</button>
            <button class="btn btn-sm btn-primary" onclick="saveChar(${ch.id})">Save</button>
          </div>
          <div class="clone-section">
            <label>Or upload reference audio (WAV) for voice cloning:</label>
            <input type="file" accept=".wav" onchange="uploadRef(event, ${ch.id})">
          </div>
        </div>
      </div>`;
    })
    .join("");

  chars.forEach((ch) => {
    ["g", "a", "p", "ac"].forEach((prefix) => {
      const el = document.getElementById(`${prefix}-${ch.id}`);
      if (el) {
        el.addEventListener("change", () => updateInstructPreview(ch.id));
      }
    });
    updateInstructPreview(ch.id);
  });
}

async function saveChar(charId) {
  const instruct = updateInstructPreview(charId);
  const gender = document.getElementById(`g-${charId}`)?.value || "female";

  const r = await fetch(`/api/books/${BOOK_ID}/characters/${charId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruct, gender }),
  });
  const d = await r.json();
  if (d.ok) {
    flashSaved(document.getElementById(`ins-${charId}`));
  } else if (d.error) {
    alert(`Save failed: ${d.error}`);
  }
}

async function previewChar(charId) {
  const instruct = updateInstructPreview(charId);
  const r = await fetch(`/api/books/${BOOK_ID}/characters/${charId}/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruct }),
  });
  const d = await r.json();
  if (d.error) {
    alert(`Preview failed: ${d.error}`);
    return;
  }
  previewAudio.src = `${d.audio_url}?t=${Date.now()}`;
  await previewAudio.play();
}

async function uploadRef(event, charId) {
  const file = event.target.files[0];
  if (!file) return;

  const fd = new FormData();
  fd.append("file", file);

  const r = await fetch(`/api/characters/${charId}/ref-audio`, {
    method: "POST",
    body: fd,
  });
  const d = await r.json();
  if (d.ok) {
    alert("Reference audio saved for cloning.");
  } else if (d.error) {
    alert(`Upload failed: ${d.error}`);
  }
}

async function saveNarrator() {
  const instruct = updateNarratorPreview();
  const r = await fetch(`/api/books/${BOOK_ID}/narrator`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruct }),
  });
  const d = await r.json();
  if (d.ok) {
    flashSaved(document.getElementById("narrator-instruct-preview"));
  } else if (d.error) {
    alert(`Save failed: ${d.error}`);
  }
}

async function previewNarrator() {
  const instruct = updateNarratorPreview();
  const r = await fetch(`/api/books/${BOOK_ID}/characters/narrator/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruct }),
  });
  const d = await r.json();
  if (d.error) {
    alert(`Preview failed: ${d.error}`);
    return;
  }
  previewAudio.src = `${d.audio_url}?t=${Date.now()}`;
  await previewAudio.play();
}

document.querySelector('.preview-btn[data-char-id="narrator"]').onclick = previewNarrator;

initNarratorControls();
loadCharacters();

window.saveChar = saveChar;
window.previewChar = previewChar;
window.uploadRef = uploadRef;
window.saveNarrator = saveNarrator;
