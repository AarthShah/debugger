# AI Debugger

Web-based minimal Python fixer with Google Gemini (2.5-pro by default).

## Quick start

```bash
source .venv/bin/activate
pip install -U flask google-genai google-generativeai
export GOOGLE_API_KEY=YOUR_KEY
PORT=5174 python webapp/server.py
```

Open: `http://localhost:5174/`

## Notes
- Uses your venv Python to run code (so installed packages like `pygame` work).
- No forced timeouts; models and runs respect provided values.
- Vision flow: use Screenshot + Submit to compare UI and auto-apply suggested edits.