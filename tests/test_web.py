"""Web API tests with a fake Bedrock client (no AWS needed)."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from sql_agent import web
from sql_agent.database import Database, create_sample_database
from tests.test_agent import FakeBedrock, final, tool_use


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = Database(create_sample_database(tmp_path / "web.db"))
    fake = FakeBedrock([])
    monkeypatch.setattr(web, "_state", {"db": db, "client": fake})
    web._sessions.clear()
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    return TestClient(web.app), fake


def events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_index_and_config(client):
    c, _ = client
    assert "SQL Agent" in c.get("/").text
    cfg = c.get("/api/config").json()
    assert "orders" in cfg["tables"] and any(m["alias"] == "gpt" for m in cfg["models"])


def test_chat_streams_steps_and_keeps_history(client):
    c, fake = client
    fake.responses += [
        tool_use("t1", "run_sql", {"sql": "SELECT COUNT(*) FROM customers"}),
        final("There are 40 customers."),
        final("Follow-up answer."),
    ]
    evs = events(c.post("/api/chat", json={"message": "How many customers?", "model": "qwen", "session_id": "s1"}))
    assert [e["type"] for e in evs] == ["tool_call", "tool_result", "answer", "done"]
    assert json.loads(evs[1]["output"])["rows"] == [[40]]

    c.post("/api/chat", json={"message": "And orders?", "model": "qwen", "session_id": "s1"})
    assert len(fake.requests[-1]["messages"]) == 5  # q, toolUse, toolResult, answer, follow-up

    fake.responses.append(final("from gpt"))
    c.post("/api/chat", json={"message": "hi", "model": "gpt", "session_id": "s1"})
    assert fake.requests[-1]["modelId"] == "openai.gpt-oss-120b-1:0"
    assert len(fake.requests[-1]["messages"]) == 1  # switching model starts fresh


def test_bedrock_error_is_reported(client):
    c, fake = client
    evs = events(c.post("/api/chat", json={"message": "q", "model": "qwen"}))  # no scripted response -> IndexError
    assert evs[-1]["type"] == "error"


def test_unknown_model_rejected(client):
    c, _ = client
    assert c.post("/api/chat", json={"message": "q", "model": "nope"}).status_code == 400


def test_basic_auth(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("APP_USERNAME", "admin")
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert c.get("/").status_code == 401
    assert c.get("/health").status_code == 200
    token = base64.b64encode(b"admin:s3cret").decode()
    assert c.get("/", headers={"Authorization": f"Basic {token}"}).status_code == 200
