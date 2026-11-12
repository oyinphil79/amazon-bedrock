"""Offline tests: a scripted fake Bedrock client drives the real agent loop and database."""

import json

import pytest

from sql_agent import SQLAgent, resolve_model_id
from sql_agent.database import Database, QueryError, create_sample_database


@pytest.fixture()
def db(tmp_path):
    return Database(create_sample_database(tmp_path / "test.db"))


class FakeBedrock:
    """Returns pre-scripted Converse responses and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def converse(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))  # snapshot
        return self.responses.pop(0)


def tool_use(tool_id, name, tool_input, text=None):
    content = [{"text": text}] if text else []
    content.append({"toolUse": {"toolUseId": tool_id, "name": name, "input": tool_input}})
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 100, "outputTokens": 20},
    }


def final(text):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 150, "outputTokens": 30},
    }


MULTI_JOIN_SQL = """
SELECT e.first_name || ' ' || e.last_name AS rep, r.name AS region,
       ROUND(SUM(oi.quantity * oi.unit_price * (1 - oi.discount)), 2) AS revenue
FROM order_items oi
JOIN orders o    ON o.order_id = oi.order_id
JOIN customers c ON c.customer_id = o.customer_id
JOIN employees e ON e.employee_id = c.sales_rep_id
JOIN regions r   ON r.region_id = e.region_id
WHERE o.status = 'delivered'
GROUP BY rep, region ORDER BY revenue DESC LIMIT 1
"""


def test_multi_level_join_flow(db):
    fake = FakeBedrock([
        tool_use("t1", "describe_table", {"table": "customers"}, text="Let me check the schema."),
        tool_use("t2", "run_sql", {"sql": MULTI_JOIN_SQL}),
        final("Pierre Dubois (Europe) generated the most revenue."),
    ])
    agent = SQLAgent(db, model="gpt", client=fake)
    result = agent.ask("Which sales rep generated the most revenue?")

    assert result.answer.startswith("Pierre Dubois")
    assert result.model_id == "openai.gpt-oss-120b-1:0"
    assert result.iterations == 3
    assert result.sql_queries == [MULTI_JOIN_SQL]
    assert result.input_tokens == 350

    # The query result fed back to the model contains the real joined rows.
    rows = json.loads(result.tool_calls[1].output)["rows"]
    assert rows[0][:2] == ["Pierre Dubois", "Europe"]

    # Converse request shape: tools offered, tool results sent back with matching IDs.
    first = fake.requests[0]
    assert {t["toolSpec"]["name"] for t in first["toolConfig"]["tools"]} == {"list_tables", "describe_table", "run_sql"}
    tool_result_msg = fake.requests[2]["messages"][-1]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"][0]["toolResult"]["toolUseId"] == "t2"


def test_sql_error_is_returned_to_model_for_self_correction(db):
    fake = FakeBedrock([
        tool_use("t1", "run_sql", {"sql": "SELECT revenue FROM orders"}),
        tool_use("t2", "run_sql", {"sql": "SELECT COUNT(*) FROM orders"}),
        final("There are 300 orders."),
    ])
    result = SQLAgent(db, model="qwen", client=fake).ask("How many orders?")

    assert result.tool_calls[0].is_error
    error_result = fake.requests[1]["messages"][-1]["content"][0]["toolResult"]
    assert error_result["status"] == "error"
    assert "no such column" in error_result["content"][0]["text"]
    assert json.loads(result.tool_calls[1].output)["rows"] == [[300]]


def test_switching_models(db):
    fake = FakeBedrock([final("a"), final("b")])
    agent = SQLAgent(db, model="qwen", client=fake)
    agent.ask("q1")
    agent.set_model("gpt")
    agent.ask("q2")
    assert [r["modelId"] for r in fake.requests] == ["qwen.qwen3-32b-v1:0", "openai.gpt-oss-120b-1:0"]


def test_reasoning_is_stripped_from_answer(db):
    fake = FakeBedrock([{
        "output": {"message": {"role": "assistant", "content": [
            {"reasoningContent": {"reasoningText": {"text": "hidden"}}},
            {"text": "<think>also hidden</think>The answer is 42."},
        ]}},
        "stopReason": "end_turn",
    }])
    assert SQLAgent(db, client=fake).ask("q").answer == "The answer is 42."


def test_max_iterations(db):
    fake = FakeBedrock([tool_use(f"t{i}", "list_tables", {}) for i in range(3)])
    result = SQLAgent(db, client=fake, max_iterations=3).ask("loop forever")
    assert "Stopped after 3 steps" in result.answer


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "UPDATE products SET unit_price = 0",
    "DROP TABLE orders",
    "SELECT 1; DELETE FROM orders",
    "WITH x AS (SELECT 1) DELETE FROM orders",
])
def test_writes_are_blocked(db, sql):
    with pytest.raises(QueryError):
        db.run_query(sql)
    assert db.run_query("SELECT COUNT(*) FROM orders")["rows"] == [[300]]


def test_row_limit(db):
    small = Database(db.path, max_rows=5)
    result = small.run_query("SELECT * FROM order_items")
    assert result["row_count"] == 5 and result["truncated"]


def test_resolve_model_id_passthrough():
    assert resolve_model_id("qwen") == "qwen.qwen3-32b-v1:0"
    assert resolve_model_id("us.anthropic.some-model") == "us.anthropic.some-model"
