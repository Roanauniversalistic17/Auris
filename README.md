# Auris

An offline audiobook reader powered by [OmniVoice](https://github.com/k2-fsa/OmniVoice) — a diffusion-based TTS model supporting 646 languages and voice cloning. Import an EPUB, PDF, or TXT file and the app automatically detects characters, assigns each one a distinct voice, injects expressive non-verbal cues, and reads the book aloud while highlighting words in sync.

Everything runs locally. No API keys. No internet required after setup.

---

## Features

**Reading**
- Import EPUB, PDF, and plain-text files
- Automatic chapter, prologue, epilogue, foreword, appendix, and part detection
- Four reading themes: Night, Sepia, Paper, AMOLED
- Adjustable font family (serif / sans-serif / monospace), size, and line spacing
- Chapter progress bar and estimated reading time
- Bookmarks with a collapsible sidebar panel

**Playback**
- Word-level highlight synchronized to audio playback
- Per-character voices with deterministic assignment — same name always gets the same voice
- Gender detection via name dictionary and pronoun co-occurrence
- Auto-scroll to the active sentence
- Playback speed control (0.5× – 2.0×)
- Pre-fetches the next segment while the current one plays

**OmniVoice integration**
- Non-verbal tag injection from narration cues: `[laughter]`, `[sigh]`, `[question-en]`, `[surprise-*]`, `[dissatisfaction-hnn]`, `[confirmation-en]`
- Whisper mode for murmured dialogue
- Scene-pacing speed modifiers (action scenes play faster, slow scenes slower)
- Voice cloning — upload a reference WAV per character in Voice Studio
- Full voice design via instruct strings: gender, age, pitch, accent

**Export**
- Single chapter, chapter-wise ZIP, or full book
- Audio: WAV (lossless) or MP3 (requires ffmpeg)
- Subtitles: ASS with per-character colour-coded styles, or SRT
- Subtitle timing derived from exact audio sample counts

**Setup**
- One-click installer detects CUDA version, Apple Silicon (MPS), or CPU and installs the correct PyTorch build automatically
- Offline-capable: all wheels can be pre-downloaded to a `wheels/` folder
- Model path configurable via Settings — use a local directory or download from HuggingFace with a progress bar

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.10 or later | 3.11 / 3.12 also work |
| ffmpeg on PATH | Optional — only needed for MP3 export |
| NVIDIA GPU (CUDA 11.8+) | Optional — CPU inference works, just slower |
| ~4 GB disk for the model | Downloaded once to a path you choose |

---

## Installation

Clone the repository:

```bash
git clone https://github.com/nikhilprasanth/omnivoice-reader.git
cd omnivoice-reader
```

Run the one-click installer. It detects your hardware and installs the correct PyTorch build, OmniVoice, spaCy, and all other dependencies:

```bash
# Windows
reader\setup.bat

# Linux / macOS
bash reader/setup.sh
```

Or run directly:

```bash
python reader/setup.py
```

The installer will:
1. Detect CUDA version via `nvidia-smi` (or `nvcc` as fallback), Apple Silicon, or fall back to CPU
2. Install the matching PyTorch + torchaudio build from the official PyTorch index
3. Install OmniVoice and all runtime dependencies
4. Download the spaCy `en_core_web_sm` language model

---

## Model Setup

The OmniVoice model weights are not included in this repository. You have two options:

**Option A — Download via the app**

Launch the app, go to **Settings**, select *Download from HuggingFace*, enter a destination path, and click *Start Download*. The app streams the model files from `k2-fsa/OmniVoice` with a live progress bar. A mirror endpoint (e.g. `https://hf-mirror.com`) can be entered for restricted networks.

**Option B — Point to an existing local copy**

If you have already downloaded the model, go to **Settings**, select *Local path*, enter the directory that contains `config.json` and `model.safetensors`, and click *Check* to verify. Then save.

The model loads in the background when the app starts — you can browse your library and read while it loads.

---

## Running the app

```bash
# Windows
reader\run.bat

# Linux / macOS
bash reader/run.sh

# Or directly
python reader/app.py
```

Open a browser at **http://127.0.0.1:7860**

---

## Usage

1. **Import** — Click *Import Book* on the library page and select an EPUB, PDF, or TXT file. Chapters are detected automatically. Character detection runs in the background.

2. **Read** — Open a book and select a chapter from the sidebar. Text is rendered as individually clickable sentences.

3. **Listen** — Press Space or click the play button. Each sentence is highlighted as it is spoken. Click any sentence to jump to it.

4. **Voice Studio** — Accessible from the reader sidebar. Adjust gender, age, pitch, and accent for each detected character, or upload a reference WAV for voice cloning. Changes take effect on the next playback.

5. **Export** — Click the Export button in the playback bar, choose scope (chapter / chapter-wise ZIP / full book), audio format, and subtitle format, then click *Generate & Download*.

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` | Play / Pause |
| `←` | Previous segment |
| `→` | Next segment |
| `B` | Add bookmark at current position |
| `T` | Cycle reading theme |
| `C` | Toggle contents sidebar |
| `M` | Toggle bookmarks panel |
| `?` | Show shortcut reference |
| `Esc` | Close overlay |

---

## Project structure

```
omnivoice-reader/
└── reader/
    ├── app.py                  Flask application and all API routes
    ├── setup.py                One-click installer with hardware detection
    ├── requirements.txt        Python dependencies (excluding PyTorch and OmniVoice)
    ├── run.bat / run.sh        Platform launchers
    ├── core/
    │   ├── database.py         SQLite schema — books, chapters, characters, segments, bookmarks
    │   ├── settings.py         JSON settings persistence and HuggingFace downloader
    │   ├── tts_engine.py       OmniVoice wrapper with async loading and audio cache
    │   ├── characters.py       spaCy NER + pronoun-based gender detection + voice profiles
    │   ├── enrichment.py       Non-verbal tag injection, whisper detection, scene-pacing
    │   ├── exporter.py         WAV/MP3 + ASS/SRT export with exact sample-count timing
    │   ├── structure.py        Chapter/section type classification
    │   └── parser/             EPUB (ebooklib), PDF (PyMuPDF), and TXT parsers
    ├── templates/              Jinja2 HTML templates
    └── static/
        ├── css/style.css       Single stylesheet with four CSS-variable-driven themes
        └── js/                 reader.js, library.js, settings.js, voice_studio.js
```

---

## Configuration

All settings persist to `reader/data/settings.json` and are editable through the Settings page. Available options:

| Setting | Default | Notes |
|---|---|---|
| `model_path` | `../model_backup/OmniVoice` | Path to OmniVoice model directory |
| `narrator_instruct` | `female, middle-aged, moderate pitch, american accent` | Default narrator voice design |
| `audio_format` | `wav` | `wav` or `mp3` |
| `subtitle_format` | `ass` | `ass` (styled) or `srt` (plain) |
| `theme` | `night` | `night`, `sepia`, `paper`, or `amoled` |
| `font_family` | `serif` | `serif`, `sans`, or `mono` |
| `font_size` | `18` | Pixels, range 13–30 |
| `line_height` | `1.9` | Range 1.4–2.2 |

---

## Offline installation

If the target machine has no internet access, pre-download all wheels on a connected machine using the preserved `wheels/` folder approach described below.

On a connected machine with the same OS and Python version, download all dependencies as wheel files:

```bash
pip download torch torchaudio --index-url https://download.pytorch.org/whl/cu124 -d wheels/
pip download -r reader/requirements.txt -d wheels/
pip download omnivoice -d wheels/
```

Copy the `wheels/` folder to the target machine alongside the repo. The installer detects the folder automatically and installs from it without any network access.

---

## Dependencies

| Package | Purpose |
|---|---|
| [OmniVoice](https://github.com/k2-fsa/OmniVoice) | TTS engine — 646-language diffusion model |
| [Flask](https://flask.palletsprojects.com/) | Web server |
| [ebooklib](https://github.com/aerkalov/ebooklib) | EPUB parsing |
| [PyMuPDF](https://pymupdf.readthedocs.io/) | PDF parsing |
| [spaCy](https://spacy.io/) | Named entity recognition for character detection |
| [pydub](https://github.com/jiaaro/pydub) | MP3 encoding (wraps ffmpeg) |
| [soundfile](https://python-soundfile.readthedocs.io/) | WAV I/O |
| [PyTorch](https://pytorch.org/) | OmniVoice inference backend |

---

## License

MIT
