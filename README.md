# Babilious

**An expressive AI companion and portfolio manager for Abubakar Mazhar.**

Babilious is a small web application that gives Abubakar's portfolio a conversational interface. Visitors can ask about his background, projects, technical skills, experience, and ambitions instead of reading a static résumé.

## Interface

- Dark, modern chat interface inspired by contemporary AI assistants
- Responsive desktop and mobile layout
- Cute puppy mascot with expressive visual identity
- Quick prompts for common portfolio questions
- Persistent conversation history during a session
- Clear/new-chat controls
- Theme toggle
- Spotify integration point for future music playback

## Architecture

```text
Browser
   │
   ▼
Flask Web App
   │
   ├── Chat API (/api/chat)
   │      │
   │      ▼
   │   Babilious Engine
   │      │
   │      ▼
   │   Local Knowledge Base
   │
   └── Static UI / Mascot / Music Controls
```

## Current AI layer

The current local version uses a lightweight retrieval engine rather than requiring a paid model API. Questions are routed to relevant Markdown knowledge files and returned as concise answers.

The project also keeps a separate persona definition in `character.py`, making it straightforward to connect a hosted LLM/agent later without redesigning the interface.

## Run locally

```bash
python -m venv venv
```

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:5000`.

## Project structure

```text
babilious-ai/
├── app.py                 # Flask application and chat API
├── babilious_engine.py    # Local response engine
├── character.py           # Babilious persona definition
├── knowledge/             # Portfolio knowledge
├── knowledge_retriever.py # Knowledge routing/search
├── memory/                # Conversation memory components
├── templates/             # Web interface
└── static/                # CSS and mascot assets
```

## Roadmap

- [x] Modern conversational interface
- [x] Local portfolio knowledge retrieval
- [x] Session chat history
- [x] Responsive UI
- [x] Puppy mascot
- [ ] Hosted LLM/agent backend
- [ ] Spotify playback integration
- [ ] Automatic music pause/resume during conversation
- [ ] Voice interaction

## Project philosophy

Babilious is intentionally more personal than a standard portfolio chatbot: the interface should feel like an actual companion while remaining grounded in accurate information about the engineer behind the portfolio.
