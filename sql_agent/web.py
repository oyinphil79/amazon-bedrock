"""Web chat UI for the SQL agent.

Run locally:
    python -m uvicorn sql_agent.web:app --reload --port 8000
    -> open http://localhost:8000

Environment variables:
    BEDROCK_REGION        Bedrock region (falls back to AWS_REGION, then eu-north-1)
    SQL_AGENT_DB          SQLite path (default sample.db, auto-created if missing)
    SQL_AGENT_MODEL       default model alias (default qwen)
    APP_USERNAME / APP_PASSWORD   if both set, the whole app requires HTTP Basic auth
                                  (always set these when the app is public)
"""

from __future__ import annotations

import base64
import json
import os
import queue
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .agent import SQLAgent
from .config import DEFAULT_MODEL, DEFAULT_REGION, MODELS, resolve_model_id
from .database import Database, create_sample_database

STATIC_DIR = Path(__file__).parent / "static"
DB_PATH = Path(os.environ.get("SQL_AGENT_DB", "sample.db"))
MAX_SESSIONS = 500
SESSION_TTL_SECONDS = 60 * 60

app = FastAPI(title="Bedrock SQL Agent")

_state: dict[str, Any] = {}
_sessions: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_sessions_lock = threading.Lock()


def _db() -> Database:
    if "db" not in _state:
        if not DB_PATH.exists():
            create_sample_database(DB_PATH)
        _state["db"] = Database(DB_PATH)
    return _state["db"]


def _bedrock_client():
    if "client" not in _state:
        import boto3
        from botocore.config import Config

        _state["client"] = boto3.client(
            "bedrock-runtime",
            region_name=DEFAULT_REGION,
            config=Config(read_timeout=120, retries={"max_attempts": 4, "mode": "adaptive"}),
        )
    return _state["client"]


def _session_history(session_id: str, model_id: str) -> list[dict[str, Any]]:
    """Per-browser conversation memory (in-process; resets when the server restarts)."""
    now = time.time()
    with _sessions_lock:
        for sid in [s for s, v in _sessions.items() if now - v["updated"] > SESSION_TTL_SECONDS]:
            del _sessions[sid]
        session = _sessions.get(session_id)
        # Reasoning blocks from one model can't always be replayed to another: start fresh on switch.
        if session is None or session["model_id"] != model_id:
            session = {"history": [], "model_id": model_id, "updated": now}
            _sessions[session_id] = session
        session["updated"] = now
        _sessions.move_to_end(session_id)
        while len(_sessions) > MAX_SESSIONS:
            _sessions.popitem(last=False)
        return session["history"]


# ---------------------------------------------------------------- auth

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    user, password = os.environ.get("APP_USERNAME"), os.environ.get("APP_PASSWORD")
    if not (user and password) or request.url.path == "/health":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            given_user, _, given_pw = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:
            given_user, given_pw = "", ""
        if secrets.compare_digest(given_user, user) and secrets.compare_digest(given_pw, password):
            return await call_next(request)
    return Response("Authentication required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="SQL Agent"'})


# ---------------------------------------------------------------- routes

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    model: str = DEFAULT_MODEL
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex, max_length=64)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def config():
    db = _db()
    return {
        "models": [{"alias": a, "model_id": m} for a, m in MODELS.items()],
        "default_model": DEFAULT_MODEL,
        "region": DEFAULT_REGION,
        "tables": db.list_tables(),
    }


@app.post("/api/reset")
def reset(body: dict):
    with _sessions_lock:
        _sessions.pop(str(body.get("session_id", "")), None)
    return {"ok": True}


@app.post("/api/chat")
def chat(req: ChatRequest):
    """Streams newline-delimited JSON events: thought, tool_call, tool_result, answer/error, done."""
    if req.model not in MODELS:
        raise HTTPException(400, f"Unknown model '{req.model}'. Choose one of: {', '.join(MODELS)}")
    events: "queue.Queue[dict | None]" = queue.Queue()

    def on_event(kind: str, data: dict) -> None:
        if kind == "tool_result" and len(data.get("output", "")) > 4000:
            data = {**data, "output": data["output"][:4000] + "…"}
        events.put({"type": kind, **data})

    def worker() -> None:
        try:
            agent = SQLAgent(_db(), model=req.model, client=_bedrock_client(), on_event=on_event)
            history = _session_history(req.session_id, resolve_model_id(req.model))
            result = agent.ask(req.message, history=history)
            events.put({
                "type": "done",
                "model_id": result.model_id,
                "steps": result.iterations,
                "sql_queries": len(result.sql_queries),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            })
        except Exception as exc:  # surface Bedrock/auth errors to the UI
            # Drop the failed turn so the next message starts from a valid conversation.
            with _sessions_lock:
                _sessions.pop(req.session_id, None)
            events.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while (event := events.get()) is not None:
            yield json.dumps(event, default=str) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
