# Build Status

_Last updated: 2026-11-14 · Milestones **1–6** complete: every part of the original plan is built_

## Milestone 6: Research & Integrations (complete)

### Completed
- [x] **Web research with numbered sources**:
  - In chat: "research solid-state batteries", "quick research on Rust vs Go", "deep research into transformer attention", "find papers on radar gesture recognition", "what does the web say about X", "what's the latest on X".
  - Also from the **Research view**: topic, depth (quick 3 / normal 5 / deep 7 sources), include arXiv papers, live progress (which pages were read or skipped and why), and the streamed summary.
  - **Sources, all free with no keys:**
    - DuckDuckGo's HTML results. When DuckDuckGo blocks automated queries (it did during testing), it falls back to Bing read through headless Edge for 30 minutes.
    - The Wikipedia API (only articles whose title matches the topic).
    - arXiv (for academic topics or when asked).
    - Video, social and shop pages, and file downloads, are skipped.
  - **How pages are read:** in parallel over plain HTTP with local text extraction. JavaScript-only pages are rendered in headless Edge.
  - **What the model sees:** only the passages that match the topic, within the context budget, numbered [1]…[n]. Pages that don't actually cover the topic are dropped.
  - **Saved locally:**
    - Every research run is stored in the `research` table and listed in the Research view, where you can expand it, search it, delete it or save it as a #research note.
    - It's included in history search.
    - Follow-up questions in the same chat get the research as context.
  - **Readable output:** citations show as small badges, sources as clickable links, and spoken replies skip the [n] numbers.
- [x] **Honesty checks, enforced in code:**
  - Every listed source was fetched in that run.
  - Citation numbers with no matching source are removed.
  - **Figures in the summary that appear in none of the pages are flagged** ("treat them as unverified").
  - A summary with no citations gets a warning.
  - Offline gives "no internet, I won't fake web results" plus matching local notes, files and earlier research.
  - Ollama offline gives each source's own text, unsummarised.
  - Pages that don't cover the topic produce a refusal rather than a summary.
- [x] **Music** (one interface, two backends, in `babilious/integrations/music.py`):
  - **Windows media session** (no account): "what's playing?", and pause/play/skip report the actual song ("Paused: Bohemian Rhapsody by Queen (Spotify)"). Works with Spotify, browsers and most players, with media keys as the last fallback.
  - A **music card** in the side panel shows now playing (with artwork via Spotify) and has prev/play-pause/next buttons. It is refreshed every 20 s while the page is visible.
  - **Spotify Web API** (optional) through *your own* free developer app:
    - Connects with **PKCE OAuth**: no client secret stored, a one-time state value protects the callback, and the token lives in `data/spotify_token.json`.
    - Adds "play Bohemian Rhapsody", "play the album ..." and "play music by ...".
    - Spotify only allows playback control for Premium accounts. On Free, it says so honestly and opens the Spotify app's search instead.
  - **Pause around conversations:** music pauses when you start talking (wake word or push-to-talk) and resumes afterwards. It only resumes what *it* paused, and you can switch this off.
- [x] **Git & GitHub help**:
  - Commands:
    - "git status of babilious", "do I have uncommitted changes?"
    - "what did I change in babilious?" and "write a commit message for babilious" (reads a diff spread across all files, then the model drafts)
    - `commit babilious with message "..."`
    - "push babilious to github"
    - "show open issues for babilious"
  - Status, diff and issues run immediately.
  - **Commit needs approval** and never pushes. **Push is "danger"** and needs a click; a spoken "yes" can't approve it.
  - Issues come from `gh` when it's signed in (this PC: yes), or else from GitHub's public API.
- [x] **Calendar feed (Google/Outlook/iCloud, read-only)**:
  - Paste the calendar's secret iCal address in Settings > Integrations.
  - Events appear on the Calendar (📅) and in the morning briefing ("Calendar today: ...").
  - Supports time zones, all-day events, folded lines, cancelled events, and repeats (daily/weekly with days/monthly/yearly, COUNT/UNTIL, EXDATE).
  - Cached for 15 minutes. A broken link never breaks the briefing.
- [x] **Optional local vision model**:
  - "Describe my screen", "what do you see?", "describe image C:\...\photo.png".
  - Uses a vision model you choose in Settings > Integrations. `moondream` (1.7 GB) and `qwen2.5vl:3b` are in the model catalog.
  - Off by default, with an honest message pointing to the download. OCR stays the default for reading text and errors.
  - It uses a 1-minute keep-alive so the RAM goes back to the chat model.
- [x] **Model switching polish:** vision-only models are labelled in Models and never picked as "the faster model".
- [x] **Settings > Integrations** tab: Spotify setup steps with the exact redirect URI to copy, Connect/Disconnect, pause-for-voice; calendar link with Refresh and status; git/gh status; vision model picker; default research depth. All of these also appear on the Health page as optional ("pending" when not set up, never an error).
- [x] The "not built yet" answers are gone: every ability in the original plan now exists.

### Measured on this laptop (llama3.2 3B, CPU)
- **"Research mmWave radar for cars"** (normal, 5 sources):
  - Search took 3 s, reading the pages 4 s, and the summary 78 s, of which 42 s was processing the ~2,300-token prompt.
  - About **85 s** in total.
- **"Quick research on solid state batteries"** in the real chat:
  - 3 sources (Wikipedia, SciTechDaily, InsideEVs) and 11 citation badges.
  - All the figures were found in the pages (no warnings).
  - Spoken through Piper.
- **Git commands:**
  - "git status" in chat correctly reported this repo's 22 changes and no upstream.
  - "write a commit message" took about 60 s.
  - The first version only saw README/.gitignore because the diff was cut at the start. Each file now gets a share of the diff, and the message covers the whole change set.

### Known limitations
- **Research is slow on CPU**: about 1–1.5 minutes, mostly prompt processing. Quick depth is faster. The summary is only as good as a 3B model: it can still word things loosely. Figures are checked automatically, but claims aren't, so the source links are the ground truth.
- **Search can be throttled.** DuckDuckGo rate-limits automated queries; the Bing-in-Edge fallback is slower (~2 s) and its page layout could change. Some sites block plain requests (e.g. MDPI returned 403); those are skipped and shown as skipped.
- **Spotify setup takes ~3 minutes** on developer.spotify.com, and starting songs needs Premium. Not tested against a real Spotify account here; the OAuth and API calls are covered by tests with recorded responses.
- **Windows media session** sees whatever app Windows considers current (e.g. a paused Chrome video), not necessarily Spotify.
- **Calendar is read-only.** **Gmail isn't connected**: reading mail needs a Google Cloud app with restricted scopes and a security review, which isn't practical for a free personal setup.
- **Vision wasn't tested with a real vision model** (none installed; moondream is a 1.7 GB optional download). The tool path is tested with a fake model.
- **Commit messages are drafts**: the model may describe a change loosely, so read before approving the commit.

### Testing (Milestone 6)
- `pytest`: **283 passed** (69 new in `tests/test_research.py` and `tests/test_integrations.py`):
  - **Research:** request parsing, page-text extraction, passage selection, citation cleanup, figure checking, and DuckDuckGo/Wikipedia/arXiv parsers with recorded responses, including the blocked-DuckDuckGo fallback. Full runs with sources read and skipped, plus the offline, Ollama-offline and no-relevant-pages cases. Also the chat and API paths, history search, and spoken text.
  - **Music and Spotify:** the media session, pause-around-voice that only resumes what it paused, the Spotify PKCE login (forged and reused state, refresh without a secret), play-search, and the Free-account fallback.
  - **Git:** status, diff, commit approval and push-is-danger on a temporary repo.
  - **Calendar:** iCal parsing and repeats, the briefing, and a broken feed.
  - **Vision:** off and on, plus model labelling.
  - **Integrations overview and health.**
- Tests never touch the internet, your media session or a real Spotify login (stubbed in `conftest.py`).
- In the real app (browser pane): research from chat with streamed citations and source links, the Research view (saved entries, expand), Settings > Integrations, the music card showing the real Windows media session, and "git status", "what's playing?" and "write a commit message" against the real model.

## Milestone 5: Personal Manager (complete)

### Completed
- [x] **Natural-language times** (our own parser, no dependency):
  - Relative and named times: "tomorrow at 9", "in 2 hours", "in an hour and a half", "tonight", "this evening", "at noon".
  - Days and dates: "on Friday", "next Monday at 10am", "20 November", "Dec 3rd at 14:00", "20/11 at 3pm", "2026-12-01", "on the 30th", "this weekend".
  - Repeats: "every day at 8", "every weekday at 8:30", "every Monday", "monthly".
  - Sensible defaults: "at 5" means 17:00; "at 9" means 9 am unless that's already gone today; repeating reminders are always am.
  - A time that has already passed today returns nothing, so Babilious asks instead of guessing.
- [x] **Reminders**:
  - "Remind me tomorrow at 9 to continue my radar project". If you leave out the time he asks "when?" and picks up your next answer.
  - "What are my reminders?", "cancel the reminder about…", "snooze it for an hour".
  - A Reminders view with a live preview of the parsed time while you type.
- [x] **Local scheduler** (checks every 20 s):
  - Fires **Windows notifications** through the built-in toast API, with no extra package; a real one was sent on this PC successfully.
  - In-app banners with Done / +10 min, and the reminder is spoken through Piper when voice is on.
  - Repeating reminders reschedule themselves, skipping the days the PC was off rather than firing a flood.
  - **Missed reminders** are delivered when Babilious starts, marked "missed while closed".
- [x] **Daily briefing**: "good morning", "brief me", "what's my day look like". It covers overdue tasks, what's due today, today's reminders, unacknowledged reminders, habits left and what's in progress.
  - It's built from local data. The model only rephrases it, and only when there's something dated to talk about.
  - An optional morning briefing notification at a time you choose.
- [x] **Deadlines**:
  - "Create a task to finish my GitHub portfolio by Friday, high priority, for the job hunt project" sets the due date, priority and project.
  - "What's due this week?" and "what's overdue?".
  - Overdue tasks are highlighted, and "I finished the portfolio task" marks one done using fuzzy matching.
- [x] **Projects**: progress bars per project (done/total, next task, next deadline, overdue count) in Tasks.
- [x] **Planning**:
  - "Help me organise my master's applications" → the local model drafts 4–8 tasks with deadlines → an **approval card** listing them → tasks are created in a project only after you approve.
  - Duplicates and impossible dates are removed, and it retries once if the model gets stuck.
- [x] **Habits**:
  - Daily, weekday or N-times-a-week habits with streaks, a 14-day strip and one-click check-ins.
  - "Add a habit to read 20 minutes every day", "I read for 20 minutes", "how are my habits?".
- [x] **Notes**:
  - "Note that the radar uses 77 GHz #radar", "show my notes about radar".
  - Full-text search, #tags, edit and delete in Second Brain > Notes.
  - Relevant notes are used as context in chat.
- [x] **Weekly review**: "what did I accomplish this week?" covers finished tasks by project, habit check-ins, PC actions, notes, reminders and conversations, summarised from local records only.
- [x] **Searchable history**: "what did I say about radar?" plus a search box in the conversation drawer, across chats, notes, tasks, reminders and the activity log.
- [x] **Calendar**: shows tasks and reminders, with repeating reminders expanded across the month.
- [x] **Today card** in the side panel: overdue count, due today, next reminder, habits left, and Brief me / My week buttons.

### Honesty safeguards added after real-model testing
- **Invented deadlines:** with llama3.2, the first "good morning" invented deadlines ("Create first UI is overdue") for tasks that had none. Three fixes:
  1. With nothing dated, the briefing is answered directly without the model.
  2. The model gets only the agenda, not the general task list.
  3. A check replaces any reply claiming "overdue" or "due today" when the agenda has no such item. Guarded replies aren't streamed or spoken until they pass.
- **Plan failures:** the planner originally failed on about 1 in 4 goals. The model sometimes returned other JSON shapes, and sometimes got stuck repeating itself so Ollama aborted. Now other shapes are accepted, with one retry. In real tests 3 of 4 goals gave usable plans, and the fourth returned a clear "try more detail" message.

### Known limitations
- **Reminders need Babilious running.** If it's closed, they're delivered as "missed" at the next start; there's no Windows Task Scheduler entry or tray app yet.
- **Toast buttons are display-only.** The Windows notification's OK button dismisses it; Done and Snooze live in the app's banner.
- **Plans need checking.** They're only as good as a 3B model: in testing, one radar-demo task was dated after the demo. That's why plans are always approved before they're created.
- **Uncommon phrasings** aren't parsed ("a week from Tuesday", "end of next month"); Babilious asks for a clearer time.
- **No two-way calendar sync.** (Milestone 6 added a read-only Google/Outlook calendar feed.)

### Testing (Milestone 5)
- `pytest`: **214 passed** (57 new). The new tests cover:
  - 25 time phrasings and 5 unreadable or past ones; next-occurrence edge cases (Friday → Monday, 31 January → 28 February)
  - task-detail parsing
  - reminder firing once, missed delivery, repeat rescheduling, snooze/cancel/past, and the once-a-day morning briefing
  - habit streaks, including weekday habits over a weekend; fuzzy habit matching
  - notes search, update and delete; agenda and week review; history search; plan validation
  - every chat command, including the "when?" follow-up and "did you…" not being taken as a task
  - plan approval creating tasks; the quiet-day briefing skipping the model; the invented-deadline guard
  - the API (calendar expansion, settings validation)
- **With the real model:** 4 plans (3 usable) and 2 briefings (the first exposed the invented-deadline problem fixed above).
- **In the browser:**
  - created a reminder through the form (live preview "today at 17:34"), saw it in Upcoming, the Today card and the Calendar
  - it fired on time into a banner, the dog reacted, and Done (a real click) cleared it
  - "good morning" now answers accurately and instantly on your real data
  - your desktop notifications were switched off during the test and are back on

---

## Milestone 4: Computer Assistant (complete)

### How it works
```text
your request ──► clear phrasing? ──► router.py ──► tool (risk checked) ──► safe: runs now
      │                                                                └► confirm/danger: approval card
      └► action-like but unclear? ──► local model tool calling (only the ~6 relevant tools)
                                       └► always an approval card, labelled "AI suggestion"
read-type tools (screen, file, clipboard, web page) ──► their text ──► the model writes the answer
```
Every outcome is recorded as completed, suggested, failed or cancelled, both in the Automation view and in the activity log.

### The permission system
| Level | What | Examples |
|---|---|---|
| **safe**: runs when you ask | harmless, reversible, or read-only | open apps/folders/sites, list or read files, `git status`, `dir`, `pip list`, screenshots, clipboard, media/volume keys |
| **confirm**: approval card | runs code or changes something | run tests, other commands, move/rename, click in a web page, lock the PC |
| **danger**: card with a red warning | hard to undo | delete (always to the **Recycle Bin**), overwrite, installs, `git push`/`reset --hard`, shutdown, downloads, writing to files |
| **blocked**: never | catastrophic or off-limits | format/diskpart/bcdedit, wiping a drive, system registry, Windows/Program Files/AppData, Babilious' own data, typing passwords or card numbers |

- **AI suggestions always ask:** anything the AI model proposes is at least "confirm", even if the same request typed plainly would run directly.
- **Approving:** use the button (from this app's page only), or say "yes, do it" for normal-risk actions. A spoken "yes" only counts right after Babilious asked. Red-warning actions always need a click.
- **One run only:** an action can't be approved twice, and pending ones expire after 15 minutes.
- **Never overwrites:** moves refuse to replace an existing file, and new files never silently overwrite one.

### Completed
- [x] **Apps:** VS Code, PowerShell, Windows Terminal, Chrome, Edge, Spotify, Word, Excel, PowerPoint, Outlook, OneNote, Teams, MATLAB, Notepad, Calculator, Paint, Task Manager, Snipping Tool, Settings and more. Installed apps are detected (22 of 23 on this PC), and you can add your own in Automation.
- [x] **Projects:**
  - "Open my Babilious project", "...in VS Code", "...in a terminal".
  - Projects are found from your saved shortcuts, indexed folders, and folders on the Desktop or in Documents (your ParkSight project was found automatically).
- [x] **Files and folders:**
  - "Create a folder called Research (on my desktop)", "create a file called todo.txt with buy milk".
  - "What's in my Downloads folder?", "read the file C:\...".
  - "Search C:\code for python files containing parse_signal".
  - Delete and move always ask first, and deletes go to the Recycle Bin.
  - Real Desktop, Documents and Downloads locations are used, including OneDrive redirection.
- [x] **Terminal:**
  - Run commands in PowerShell, with a timeout, output captured and real exit codes.
  - Read-only commands run directly; everything else asks first.
  - **"Run the tests for my project"** detects pytest (using the project's own `.venv`), `npm test` or unittest.
  - **"Why did that fail?"** explains the last failed output.
- [x] **Screen awareness, only when you ask:**
  - "What's on my screen?", "explain this error", "read this window": a screenshot, then local OCR (RapidOCR), then an answer.
  - Typed requests wait 3 s so you can switch to the window you mean; spoken ones capture straight away.
  - The screenshot appears in the chat. The 30 most recent are kept in `data/screenshots/`, and nothing is captured in the background.
- [x] **Clipboard:** "explain what I copied", "copy that to my clipboard" (copies the code from the last reply if there is any).
- [x] **Browser:**
  - Open sites or searches in your own browser ("Open Chrome and navigate to GitHub").
  - Read any public page ("summarise https://...") with headless Edge.
  - An automation window that can go to pages, type into fields and click. It has its own profile, never types passwords or card numbers, and clicks on submit/send/buy/delete-type buttons always need approval.
- [x] **Windows control:** play/pause/next/previous through media keys, which also works with **Spotify** ("pause the music", "skip this song"); volume up/down/mute; lock the PC (asks first); open Settings pages (opens them only, never changes anything).
- [x] **Approval cards in chat** with Approve/Cancel, a risk badge, an "AI suggestion" label, output and screenshot. A pending count badge shows on the Automation tab.
- [x] **Automation view:** what's waiting for approval, one-click tries, the app list (add your own), project shortcuts, the default folder for new files, a toggle for AI suggestions, the rules in plain words, recent actions with output, and the activity log.

### Measured on this laptop
| Step | Time |
|---|---|
| Full-HD screenshot | 0.05 s |
| OCR of a screen (warm) | ~0.7 s (first run ~1.4 s) |
| Headless Edge page read | ~4.6 s (first start) |
| Model tool proposal (llama3.2) | 2–7 s, after trimming the tools sent from 22 to the ~6 relevant ones (the full set took 38 s cold) |
| `git status` through the tool | 0.3 s |

### Known limitations
- **AI suggestions need checking:** they can be wrong. In testing, "check which python packages I have" became `Get-Module` instead of `pip list`. That's exactly why they always wait for approval, so read the card before approving.
- **OCR, not real vision:** text is read reliably, but layout, images and charts aren't understood. A local vision model (e.g. `qwen2.5vl:3b` / `moondream`) could be added later, but it's heavy for 8 GB of RAM.
- **Typed screenshots capture the whole screen** after a 3-second delay. There's no "only the active window" option yet.
- **Simple automation-window actions only:** typing into a field and clicking by visible text, one step at a time. Complex multi-step web flows aren't scripted.
- **Not exercised for real on your desktop:** opening apps, real screenshots of your screen, the Recycle Bin, the clipboard and the media keys. They're covered by tests with recorders (and the clipboard/OCR/Edge parts were probed without touching your data), but not run against your actual desktop. Try "open notepad" or "take a screenshot" yourself.

### Testing (Milestone 4)
- `pytest`: **157 passed** (83 new). The new tests cover:
  - 23 command classifications and 30 routing phrasings, plus normal chat that must not be routed
  - safe vs approval vs blocked flows; model suggestions always pending; no double approval; expiry
  - real file operations in temp folders (overwrite needs approval, delete only after approval, moves never overwrite), and protected paths refused
  - **real PowerShell** runs (output, exit codes, a blocked `format c:`), and a real failing pytest project detected and run after approval
  - real OCR on a rendered error image feeding the model
  - spoken approval, stale "yes" ignored, red-warning actions needing a click
  - model tool calling with approval, and the API guards (header, screenshot path traversal, settings validation)
- **With the real model:** llama3.2 tool proposals checked on 7 requests (results above).
- **In the browser:**
  - `git status` and the folder listing ran, with result cards
  - an AI suggestion was cancelled with a real click
  - `python -c "print(6*7)"` was approved with a real click and showed output 42
  - the Automation view showed apps, projects, the pending badge and history

---

## Milestone 3: Voice (complete; real-microphone check pending on your side)

### How it works
```text
Browser mic (echo cancellation, noise suppression)
  → AudioWorklet: 16 kHz int16, 20 ms frames
  → WebSocket to 127.0.0.1 (origin-checked)
  → energy VAD → faster-whisper base.en (int8, CPU)  ──► transcript → normal chat pipeline
Reply tokens → sentence splitter → /api/voice/tts (Piper) → Web Audio queue → speaker
                                                         └► analyser → mouth movement
```
The mic is opened only while push-to-talk is held, or while hands-free is switched on (shown with a red indicator). Audio is held in memory (a 2-second rolling buffer plus the current sentence), is never written to disk, and never leaves this PC.

### Completed
- [x] **Push-to-talk**: hold the 🎙️ button, or hold **Space** when you're not typing. The mic button glows with the input level.
- [x] **Speech recognition**: faster-whisper, selectable tiny.en / base.en (default) / small.en. It loads on first use and unloads after 15 minutes idle to free RAM. Whisper's usual phantom phrases on silence ("Thank you.") are filtered out.
- [x] **Spoken replies with Piper**: the default voice is **Ryan** (deep US male); you can switch to 5 other voices, preview them, download them in-app, and set the speed. Replies are spoken **sentence by sentence while the model is still writing**, so he starts talking within about a second of the first sentence. Code blocks, URLs and file paths aren't read aloud ("I've put the code on screen"). Windows voices remain as a fallback, and "Voice off" works too.
- [x] **Real lip-sync**: the mouth opens with the loudness of the audio actually playing.
- [x] **Hands-free with a wake word** (👂 button):
  - **"Babilious"** is the default, via whisper-based spotting that works for any word you set. It handles "Babilious, what's on my list?" in one breath, or the name alone followed by a short listening window. A short beep confirms he heard the name.
  - Alternatively, **openWakeWord** with pretrained phrases (hey jarvis, hey mycroft, hey rhasspy, alexa) or your own trained `.onnx` model. It's lighter on CPU in noisy rooms but can't say "Babilious" without training.
- [x] **Barge-in**: talk over him (with echo cancellation), say his name, or press the mic or type. Speech stops immediately, any reply still being written is cancelled, and the words you'd already started saying are kept (0.8 s pre-roll).
- [x] **Back to listening**: after he answers in hands-free mode, he listens for a follow-up for 6 seconds (adjustable) without needing the wake word.
- [x] **Settings > Voice**: speech model, voice, speed, wake engine and word, barge-in mode, follow-up window, mic sensitivity, microphone and speaker selection (speaker where the browser supports it), and a mic level test.
- [x] **Visible states**: listening (ears up, sound waves), transcribing (thinking), speaking (lip-sync), "Didn't catch that" (confused), and the mic status in the side panel.
- [x] **Text-only fallback**: if the voice packages, models or mic permission are missing, the buttons say why and text chat keeps working. If the browser blocks audio until you click, he tells you rather than freezing.
- [x] **Health page** shows the whisper model, Piper voice and wake-word configuration.
- [x] **Security**: the voice WebSocket only accepts this app's own page (origin and port checked), and voice settings are validated together, so nothing is saved if any value is invalid.

### Measured on this laptop
| Step | Time |
|---|---|
| Whisper base.en first download + load | 51 s (one time) |
| Whisper load from disk | 1.3 s |
| Transcribe a 2-second command | ~0.5 s |
| Piper, per sentence (after warm-up) | 0.06× real time (a 2 s sentence in ~0.12 s) |
| openWakeWord "hey jarvis" score / unrelated speech | 0.999 / 0.075 |
| Models on disk | whisper 141 MB, Piper voice 61 MB, openWakeWord 9 MB |

**Wake-word accuracy:** with whisper prompted with the name, "Babilious" was caught in 5 of 5 test phrases. The hotwords option only managed 2 of 5. Lookalikes ("Hey Barbara", "The babies are hilarious", "Fabulous work") and noise, hum and keyboard clicks don't trigger it. The one false trigger found, where whisper echoed its prompt, is now detected and rejected.

### Known limitations
- **Not yet tried with your real microphone.** The Claude browser pane blocks mic access, so the full mic → whisper path was tested by streaming Piper-generated speech through the real WebSocket instead. Push-to-talk and hands-free still need a quick check in Edge or Chrome at http://127.0.0.1:5000.
- **Talking over him** relies on the browser's echo cancellation. On laptop speakers without headphones, a loud reply might occasionally interrupt itself; if it does, set barge-in to "Say the wake word".
- **CPU while hands-free:** whisper wake spotting transcribes each burst of speech near the mic. In a noisy room (TV, other people) that means more CPU use; openWakeWord is the lighter option there.
- **Unusual wake words:** whisper spotting works best with a distinctive name; very short or common words cause more false triggers.
- **Speaker selection** needs `AudioContext.setSinkId` (Chrome/Edge 110+); elsewhere the system default is used.
- **Hands-free stops when the page closes:** voice runs in the page, so closing the tab ends it. A background tray app would be a later step.

### Testing (Milestone 3)
- `pytest`: **74 passed** (27 new). The new tests cover:
  - wake-word matching: variants, lookalikes, custom words
  - VAD segmentation, pre-roll and length cap
  - text cleanup for speech
  - the full session state machine: push-to-talk, too-short presses, wake then command, name and command in one breath, no false wakes, follow-up timeout, barge-in with pre-roll
  - voice settings validation and atomic saves, TTS endpoint guards
  - the JavaScript sentence splitter, run in Node
  - **live end-to-end**: a real server with Piper speech streamed over the real WebSocket in 20 ms frames, with faster-whisper returning the right words; in hands-free mode, unrelated speech is ignored and "Babilious, what's on my list today?" wakes him and passes on the command; foreign origins are refused
- **In the browser:**
  - a spoken reply played as 2 Piper sentences with lip-sync, then switched to the reply mood
  - Settings > Voice and Health checked
  - a refused microphone falls back cleanly

---

## Milestone 2: Memory & Second Brain (complete)

### Completed
- [x] **Folder indexing, only for folders you pick**: native Windows folder picker (PowerShell `FolderBrowserDialog`, no extra dependency) or type a path
  - Refuses drive roots, the whole user folder, Windows/Program Files/ProgramData, AppData, and folders nested inside (or containing) an indexed one
  - Skips `node_modules`, `.git`, `.venv`, `__pycache__`, build output, hidden folders, Office lock files and Babilious' own `data/`
  - Caps at 20,000 files per folder and says so
- [x] **Readers** for TXT, Markdown, PDF (`pypdf`), DOCX (`python-docx`), Jupyter notebooks, HTML and about 40 code/config types; UTF-8, UTF-16 and cp1252 text
  - Every file is searchable by name (images, archives...); readable ones also by content
  - Unreadable files (scanned PDFs, password-protected, corrupt) are recorded with the reason and never stop the run
- [x] **SQLite FTS5 full-text index** (porter stemming) with external-content triggers so deletes stay in sync
- [x] **Incremental re-indexing** by size + modification time; removed files drop out; an automatic refresh on startup, plus manual and cancellable runs, all in a background thread with live progress
- [x] **Files view**: folder list with counts, Add/Re-index/Remove (remove = forget the index only, with confirmation), search by name and content with highlighted snippets, type filters, newest-first sort, document detail (preview, keywords, related documents, copy path, "Summarise in chat")
- [x] **Chat retrieval with citations**: relevant passages from your files go into the prompt and the reply shows clickable **Sources**. Strict relevance rules keep casual chat free of files (and of the extra CPU cost); can be turned off in Second Brain
- [x] **Chat commands**: "find the latest PDF in my Documents folder", "search for python files containing parse_signal", "show me my latest files", "summarise applications.docx", "index C:\path\to\folder"
- [x] **Document summaries**: the model gets the document text (first 5,000 chars, and says so if truncated) and answers with citations
- [x] **Connected documents**: related files via `[[wiki links]]`, Markdown links and shared keywords; shown per document and as a **document map** (force-directed canvas, laid out once so it uses no idle CPU; hover, drag, click to open)
- [x] **Optional semantic search**: with `nomic-embed-text` (~270 MB) installed and the toggle on, chunks are embedded locally and merged into chat retrieval. Off by default. Embedding models can't be selected as the chat model, and "faster model" never picks one
- [x] **Memory suggestions**: when you share a personal fact ("I'm applying to master's programs..."), a **Remember this?** button appears; nothing is saved without your click, and the model is told not to claim it saved anything
- [x] Long-term memory vs. conversation context kept separate (memories in every prompt; history limited to the last N messages); memories editable/pinnable/deletable (from Milestone 1)
- [x] Health page: second-brain stats and PDF/DOCX reader checks

### Known limitations (Milestone 2)
- **Scanned PDFs** have no text layer, so they're indexed by name only. OCR (e.g. Tesseract) would cost too much CPU to include by default.
- **Not supported**: `.doc` (old Word), `.pptx` and `.xlsx`. They're searchable by name only.
- **No live file watching**, which would cost background CPU. The index refreshes on startup and on demand.
- **Folder picker**: the Windows dialog opens on this PC and can sometimes appear behind the browser window. Typing the path always works.
- **Search ranking**: content search is keyword-based (BM25), so code and test files that repeat a term can outrank prose. Semantic search helps once it's enabled.
- **Small-model limits**: the 3B model sometimes paraphrases loosely or says "I'll keep that in mind". The sources shown under a reply are authoritative.
- **Semantic search not checked against the real model**: it was tested with a fake embedding model. `nomic-embed-text` isn't installed on this PC yet.

### Testing (Milestone 2)
- `pytest`: **47 passed** (19 new). The new tests use real sample files: Markdown with wiki links, a generated DOCX, a hand-built PDF, a UTF-16 text file, code, an image, plus `node_modules`, `.git` and Office lock files that must be skipped. They cover:
  - folder rules and incremental updates (add, modify, delete)
  - FTS consistency and broken files
  - search filters, the chat retrieval thresholds, the graph and related documents
  - semantic search (fake embedder), the file commands, summaries with citations, and memory offers that need approval
  - the API endpoints
- **Manual run against the real Ollama** (with this project folder temporarily indexed, then removed):
  - 83 files indexed in about 1.5 s; an unchanged re-index takes 0.2 s
  - "find the latest markdown files" returned correct results
  - "summarise why_hire_me.md" gave an accurate 5-bullet summary
  - "what technologies does City Sentinel use?" answered correctly, citing `projects.md`
  - memory offer shown, nothing saved silently
  - Files view, document detail and document map all checked
- Not exercised: the native folder dialog (it would pop up on your desktop) and semantic search with the real `nomic-embed-text`.

---

# Milestone 1: Foundation (complete)

## Audit of the original project (before changes)

Checkpointed in git as the first commit on `main` (`Checkpoint: original Babilious project...`).

| Finding | What was done |
|---|---|
| Flask app with keyword-routed answers from `knowledge/*.md`, no LLM | Kept as the offline fallback; LLM layer added on top |
| Hard-coded `SECRET_KEY`, `debug=True` | Secret is generated per install in `data/.secret_key`; debug off |
| All paths relative to the working directory (broke when started elsewhere) | Made absolute in every module |
| `test_adventure.py`, `test_briefing.py`, `test_cards.py` import modules that don't exist (`adventure_logger`, `expedition_briefing`, `adventure_cards`) | Left untouched for reference; excluded from pytest via `pytest.ini` |
| Other `test_*.py` files are interactive scripts (`input()`) | Same; the real suite is in `tests/` |
| JSON memory (`adventure_log.json`) and missions (`state/*.json`) | Imported once into SQLite; originals kept |
| `search_engine.py` and `knowledge_retriever.py` duplicate each other; `knowledge_loader.py`, `dashboard.py`, `ui/index.html` unused | Kept (path fixes only); superseded by `babilious/knowledge.py` and the new UI |
| `styles.css` and `static/style.css` duplicate old styles | Kept, unused; new styles in `static/css/app.css` |
| `knowledge/skills.md` is empty; `journey.md` and parts of `faq.md` are placeholders | Flagged on the Health page; content needs writing by you |
| Persona text spread over 4 files | Merged into `character.py` (sources listed in its docstring) |

## Completed features (Milestone 1)

- [x] Local LLM chat through Ollama, streamed token by token (NDJSON), with a stop button and interrupt-on-new-message
- [x] Persona stored separately from the model (`character.py` → `babilious/persona.py`), the same for every model
  - Fat, shirtless curly dog with a jaded-genius, Rick-style "doesn't care" attitude, still loyal and honest
- [x] Stable prompt prefix, so Ollama reuses its cache (warm first token ≈ 0.1 s)
- [x] Per-turn context: open tasks, long-term memories, relevant knowledge-base snippets
- [x] Offline fallback: answers from local notes when Ollama is down, and says so
- [x] New UI modelled on the reference: large circular portrait, status line, caption, voice selector + input
- [x] Animated SVG dog with 11 expression states driven by CSS (no images, no 3D); idle blinking, glancing, breathing, napping after 3 min
- [x] Sidebar: Chat, Second Brain, Files, Tasks, Calendar, Reminders, Research, Automation, Settings
- [x] Status panel: model, Ollama state, loaded model + RAM, CPU/RAM meters, current task, voice/music status, quick actions, recent activity
- [x] Conversation history in SQLite: browse, reopen, delete (with confirmation)
- [x] Long-term memories: add, edit, pin, search, delete (Second Brain view); "remember that ..." in chat
- [x] Tasks: add, prioritise, due dates, projects, in-progress/done, rename, delete; "create a task to ..." and "what's unfinished?" in chat
- [x] Calendar: month grid of tasks by due date; click a day to add a task
- [x] Model management: installed models with size, RAM estimate, loaded state, last benchmark; switch, benchmark, download (with progress), remove (with confirmation, never the active one)
- [x] Model switching by conversation ("switch to the faster model", "use the coding model", "switch to llama")
- [x] Old model unloaded from RAM when switching
- [x] Honest answers for abilities that aren't built yet (open apps, screenshots, files, research, reminders, Spotify), logged as "suggested"
- [x] Activity log with completed / suggested / failed status
- [x] Health page: Ollama, active model, database, knowledge base, voice stack, microphone, Playwright, internet
- [x] Spoken replies with local Windows voices (Web Speech API, `localService` only), adjustable rate; typing interrupts speech
- [x] Preferences: name, temperature, context size, reply length, history length, model keep-alive
- [x] Security: localhost-only, custom-header guard against cross-site requests, input length limits
- [x] Rotating log file `data/logs/babilious.log`
- [x] `setup.ps1`, `start.ps1`, `.env.example`, README

## Incomplete / not started

| Feature | Milestone | Current state in the app |
|---|---|---|
| Gmail | - | Not planned: needs a verified Google Cloud app (see Milestone 6 limitations) |

## Known limitations

- **Small-model quality.** 3B models sometimes ignore the persona's attitude or echo the example
  lines. Bigger models (phi3.5, qwen2.5:7b) do better but are slow on this CPU.
- **Cold start.** The first message after the model unloads takes about 5–10 s while it loads. Set
  "Keep model in RAM" to 30 min if you prefer speed over free RAM.
- **RAM is tight.** The laptop sits at ~90% RAM with llama3.2 loaded and normal apps open. Use
  `llama3.2:1b` when multitasking.
- **Stopping a reply** stops it in the UI immediately; Ollama may finish the current token batch in
  the background for a moment.
- **Chat commands** use pattern matching (reliable, but only for the phrasings listed in
  `babilious/intents.py`). Tool-calling by the model comes with Milestone 4.
- Mood after a reply is picked by a cheap heuristic, not by the model.

## Dependencies

Python 3.10+: Flask, requests, psutil, pypdf, python-docx, numpy, faster-whisper, piper-tts, openwakeword, flask-sock, pillow, rapidocr-onnxruntime, pyperclip, send2trash, playwright (uses installed Edge), pytest. Milestone 5 adds no new packages. Milestone 6 adds `tzdata` (time zones for calendar feeds on Windows). External: Ollama 0.40+ (installed: v0.40.14) with
`llama3.2:latest` and `qwen2.5-coder:3b`. All free and open source.

## Testing results

- `pytest`: **28 passed** (storage, migration, tasks, memories, persona, knowledge search,
  commands, model selection, all API endpoints, CSRF guard, offline fallback, errors, health).
- Manual, in the browser against the real Ollama: chat streaming, command replies, stop/interrupt,
  all 11 expressions, every sidebar view, the models list and health page.
- Benchmarks on this laptop (Settings > Models shows the latest):

| Model | Warm generation | Warm first token | Cold load |
|---|---|---|---|
| llama3.2:latest | 13.7–14.8 tok/s | 86 ms | 4.2 s |
| qwen2.5-coder:3b | 13.0 tok/s | 124 ms | 10.1 s |

## What could come next (not started)

Everything in the original plan is built. Ideas worth doing next, none of them started:
1. **Run at login / tray icon** so reminders fire even when the browser tab is closed.
2. **Real-device checks** only you can do: hands-free with your microphone, Spotify with your account, a vision model if RAM allows.
3. **Research quality**: a re-ranking step, or a larger model for deep research when the laptop is idle.
4. **Two-way calendar** via Google's API with your own OAuth app (more setup, but would allow creating events).
