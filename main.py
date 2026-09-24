"""Entry point.

Run:  uvicorn app.main:app --reload
Docs: http://127.0.0.1:8000/docs

Env:  RANGER_DB         SQLite file path (default: ranger.db)
      RANGER_ADMIN_KEY  enables PATCH /trails; send it as the X-API-Key header
"""
from .api import create_app

app = create_app()
