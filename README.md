# Babilious

**A free, local-first personal AI companion for Windows.**

Babilious is a fat, shirtless, curly Spanish Water Dog (allegedly) with the competence of a jaded
genius and none of the patience for corporate speak. He runs entirely on your laptop: a local
LLM through [Ollama](https://ollama.com), a SQLite database, and a small Flask web app. No paid
APIs, no cloud, no keys.

He started as a conversational portfolio for Abubakar Mazhar and is growing into a JARVIS-style
assistant, one milestone at a time. See [BUILD_STATUS.md](BUILD_STATUS.md) for exactly what works today.

## What works now (Milestones 1–6)

- Chat with Babilious through a local model (streamed, interruptible)
- An animated dog with 11 expressions (idle, listening, thinking, speaking, happy, excited,
  curious, confused, annoyed, sleeping, error)
- A personality that stays the same whichever model you pick
- Model management: list, switch, benchmark, download and remove Ollama models, or just say
  "switch to the faster model"
- Persistent conversations, long-term memories you can edit, tasks and a calendar
- Commands in chat: "create a task to ...", "what's unfinished?", "remember that ..."
- A dashboard with system resources, loaded model, current task and an activity log
- A health check page, plus offline fallback when Ollama isn't running
- **Second brain**: index folders you pick (TXT, Markdown, PDF, DOCX, notebooks, code), search
  names and contents, see related documents and a document map
- Answers that use your files and cite them; "summarise <file>", "find the latest PDF in my
  Documents folder", "search for python files containing <function>"
- "Remember this?" offers when you share personal facts (never saved without your click)
- Optional semantic search with a local embedding model (`nomic-embed-text`)
- **Voice**: hold the mic button or Space to talk (faster-whisper), or turn on hands-free and say
  "Babilious". He answers out loud with Piper, lip-synced, and you can interrupt him mid-sentence.
  All of it runs on this PC.
- **Computer assistant**: "open VS Code", "open my Babilious project in VS Code", "create a folder called
  Research", "what's in my Downloads folder?", "run the tests for my project", "why did that fail?",
  "what's on my screen?", "explain what I copied", "open Chrome and go to GitHub", "pause the music".
  Anything that runs code, changes or deletes files waits for your approval (deletes go to the
  Recycle Bin), and dangerous commands are blocked outright.
- **Personal manager**: "remind me tomorrow at 9 to continue my radar project" (Windows notifications, spoken
  reminders, repeats like "every weekday at 8:30"), "good morning" briefings, "what's due this week?",
  deadlines and projects with progress, habits with streaks, quick notes, "help me organise my master's
  applications" (a task plan you approve), "what did I accomplish this week?", and history search.
- **Research & integrations** (Milestone 6, all optional and free):
  - **Web research**: "research solid-state batteries", "find papers on radar gesture recognition". He searches the web, Wikipedia and arXiv, reads the pages on your PC, and writes a summary where every point links to a numbered source. Figures that aren't in the pages are flagged, and results are saved in Research.
  - **Music**: "what's playing?", pause/skip with the actual song named, and music pauses while you talk to him. Optionally connect Spotify with your own free developer app (PKCE, no secret) for "play Bohemian Rhapsody" (Premium only, Spotify's rule).
  - **Git & GitHub**: "git status of babilious", "write a commit message for babilious", "show open issues". Commits need approval; pushes always need a click.
  - **Calendar feed**: paste your Google/Outlook calendar's secret iCal link to see events on the Calendar and in your briefing (read-only).
  - **Vision**: "describe my screen" with an optional local vision model (e.g. `moondream`).

  Set these up in **Settings > Integrations**.

## Requirements

- Windows 10/11, Python 3.10+
- [Ollama](https://ollama.com/download) (free), with at least one small model (default `llama3.2`)
- About 4 GB of free RAM for a 3B model

## Install and run (PowerShell)

```powershell
cd C:\Users\ENVY\Desktop\babilious-ai-main\babilious-ai-main
powershell -ExecutionPolicy Bypass -File .\setup.ps1
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Or manually:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull llama3.2
.\.venv\Scripts\python.exe app.py
```

Then open http://127.0.0.1:5000.

## Voice

- **Talk**: hold 🎙️ (or hold Space when you're not typing), speak, release.
- **Hands-free**: click 👂, then say "Babilious, ..." (the wake word is configurable in Settings > Voice).
- **Interrupt**: start talking while he speaks, say his name, press the mic, or just type.
- **Models** download on first use into `data/models/` (about 210 MB with the defaults); `setup.ps1` can fetch them up front.
- Open the app in **Edge or Chrome** at http://127.0.0.1:5000 and allow the microphone when asked.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The suite uses a fake Ollama client, so it runs offline in a few seconds.

## Choosing a model

Measured on this laptop (i7-1195G7, 8 GB RAM, CPU only):

| Model | Generation speed | Cold start | Notes |
|---|---|---|---|
| `llama3.2:latest` (3B) | ~14 tokens/s | ~5 s | Default: best personality for the speed |
| `qwen2.5-coder:3b` | ~13 tokens/s | ~10 s | Better at code, flatter personality |
| `llama3.2:1b` | (not installed) | | Fastest option; download from Settings > Models |

7B+ models will run, but they strain 8 GB of RAM and reply slowly. Benchmark any model from
Settings > Models.

## Architecture

```text
Browser UI (templates/, static/)  ── vanilla JS modules, no build step
        │  JSON + streamed NDJSON, localhost only
        ▼
Flask app (babilious/web.py)
        │
        ├── assistant.py   orchestrator: commands → local LLM → offline fallback
        ├── intents.py     deterministic commands (tasks, memory, model switching)
        ├── persona.py     builds prompts from character.py (identity is model-independent)
        ├── llm.py         Ollama client (chat stream, list/pull/delete/unload)
        ├── models.py      model selection, catalog, benchmarks
        ├── knowledge.py   section search over knowledge/*.md
        ├── documents.py   TXT/MD/PDF/DOCX/code readers, chunking, keywords, links
        ├── indexer.py     folders, incremental FTS5 index, search, chat retrieval, graph
        ├── semantic.py    optional local embeddings + cosine search
        ├── manager/       timeparse.py (natural-language times), reminders.py (+ scheduler, notify.py),
        │                  habits, notes, briefing (agenda, week review, history), planner, commands
        ├── tools/         router.py (requests → tools), actions.py (permissions, approvals),
        │                  apps, files, terminal, screen (OCR), clipboard, browser (Playwright), wintools
        └── voice/         stt.py (faster-whisper), tts.py (Piper), vad.py, wake.py,
                           engine.py (shared models), session.py (WebSocket state machine)
        ├── memory.py      conversations + long-term memories
        ├── tasks.py       tasks / calendar data
        ├── activity.py    action log (completed / suggested / failed)
        ├── system.py      CPU/RAM status + health checks
        └── db.py          SQLite schema, settings, one-time import of the old JSON files
```

**Why Flask and not FastAPI/React?** The existing app was Flask and already worked, and streaming
works fine with it. A React/TypeScript build chain would add Node tooling and a build step for no
real gain at this size. The UI is plain ES modules that the browser loads directly, and it can
move to React later if it grows.

## Data and privacy

- Everything is stored in `data/babilious.db` (SQLite). Logs are in `data/logs/`.
- The server listens on `127.0.0.1` only, and state-changing API calls need a custom header so
  other websites can't drive it.
- The original `memory/adventure_log.json` and `state/*.json` were imported on first run and
  left untouched.
- Voice: the mic is open only while you hold push-to-talk or while hands-free is on (red indicator).
  Audio stays in memory on this PC and is discarded after transcription; nothing is recorded or uploaded.
  The voice WebSocket accepts only this app's own page.
- Computer actions: read-only things run when you ask; anything that changes or runs something shows an
  approval card first; formatting disks, system folders and password typing are refused. See Automation > Rules.
- Screenshots are taken only when you ask, stay in `data/screenshots/` (30 most recent), and are read with local OCR.
- Only folders you add in Files are read; drive roots, system folders and AppData are refused.
  Removing a folder forgets its index and never touches the files.
- Your data is never uploaded. Network calls go to your local Ollama server, plus:
  - model downloads you start yourself, and a connectivity check on the Health page;
  - **only when you ask for research**: the topic as a search query to DuckDuckGo (or Bing), Wikipedia and arXiv,
    and plain page fetches of the results (no cookies, no account);
  - **only if you set them up**: Spotify (your own app; token in `data/spotify_token.json`, deleted on Disconnect),
    your calendar's iCal link (read-only), and GitHub (`gh` or the public API, when you ask about issues or approve a push).

## Project layout

```text
app.py                 entry point
babilious/             application package (see Architecture)
character.py           Babilious' persona (identity, rules, examples)
knowledge/             Markdown notes he can search
templates/             index.html + partials/dog.svg.html (the animated dog)
static/css, static/js  UI
tests/                 pytest suite
setup.ps1, start.ps1   Windows setup / launch
personality.md, docs/, examples/, memory/, state/   original project material (kept)
```
