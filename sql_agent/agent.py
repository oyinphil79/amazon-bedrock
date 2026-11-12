"""The agentic loop: model <-> tools via the Amazon Bedrock Converse API.

    user question
         │
         ▼
    ┌─────────┐  toolUse (list_tables / describe_table / run_sql)  ┌───────────┐
    │  model  │ ─────────────────────────────────────────────────▶ │  SQLite   │
    │(Bedrock)│ ◀───────────────────────────────────────────────── │ (read-only)│
    └─────────┘            toolResult (rows or error)               └───────────┘
         │ stopReason == end_turn
         ▼
    natural-language answer

Because Converse is model-agnostic, switching between Qwen, GPT (gpt-oss) or any
other tool-capable Bedrock model is just a different ``model_id``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import DEFAULT_REGION, resolve_model_id
from .database import Database
from .tools import TOOL_SPECS, execute_tool

SYSTEM_PROMPT = """You are a careful data analyst agent with read-only access to a SQLite database.

Workflow:
1. Explore: use list_tables / describe_table to learn the schema and foreign keys you need.
2. Query: write ONE SQLite query at a time with run_sql. For multi-table questions, join
   through the foreign keys exactly as described (e.g. customers -> orders -> order_items ->
   products -> categories). Use table aliases, explicit JOIN ... ON, and GROUP BY all
   non-aggregated columns. Revenue = quantity * unit_price * (1 - discount) from order_items.
3. If a query errors or the result looks wrong, fix it and run it again.
4. Answer: reply to the user in clear natural language based ONLY on the query results.
   Include key numbers, round money to 2 decimals, and briefly mention how you computed it.
   Never invent data. If the question cannot be answered from the data, say so.

Database schema:
{schema}
"""

EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    output: str
    is_error: bool


@dataclass
class AgentResult:
    question: str
    model_id: str
    answer: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def sql_queries(self) -> list[str]:
        return [c.input.get("sql", "") for c in self.tool_calls if c.name == "run_sql"]


_THINK_TAGS = re.compile(r"<(think|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)


def _extract_text(content: list[dict[str, Any]]) -> str:
    """Join plain text blocks, skipping reasoning blocks / inline <think> sections."""
    text = "\n".join(block["text"] for block in content if "text" in block)
    return _THINK_TAGS.sub("", text).strip()


class SQLAgent:
    def __init__(
        self,
        db: Database,
        model: str = "qwen",
        region: str | None = None,
        client: Any = None,
        max_iterations: int = 12,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        include_schema_in_prompt: bool = True,
        on_event: EventCallback | None = None,
    ):
        self.db = db
        self.model_id = resolve_model_id(model)
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime", region_name=region or DEFAULT_REGION)
        self.client = client
        self.max_iterations = max_iterations
        self.inference_config = {"maxTokens": max_tokens, "temperature": temperature}
        schema = db.schema_overview() if include_schema_in_prompt else "(call list_tables to discover it)"
        self.system_prompt = SYSTEM_PROMPT.format(schema=schema)
        self.on_event = on_event or (lambda kind, data: None)

    def set_model(self, model: str) -> None:
        """Switch models on the fly (alias such as 'qwen'/'gpt' or a raw Bedrock model ID)."""
        self.model_id = resolve_model_id(model)

    def ask(self, question: str, history: list[dict[str, Any]] | None = None) -> AgentResult:
        """Answer one question. Pass ``history`` (a list you keep between calls) for follow-ups."""
        messages = history if history is not None else []
        messages.append({"role": "user", "content": [{"text": question}]})
        result = AgentResult(question=question, model_id=self.model_id, answer="")

        for _ in range(self.max_iterations):
            result.iterations += 1
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": self.system_prompt}],
                messages=messages,
                toolConfig={"tools": TOOL_SPECS},
                inferenceConfig=self.inference_config,
            )
            usage = response.get("usage", {})
            result.input_tokens += usage.get("inputTokens", 0)
            result.output_tokens += usage.get("outputTokens", 0)

            message = response["output"]["message"]
            messages.append(message)
            tool_uses = [b["toolUse"] for b in message["content"] if "toolUse" in b]

            if response.get("stopReason") != "tool_use" or not tool_uses:
                result.answer = _extract_text(message["content"]) or "(the model returned no answer)"
                self.on_event("answer", {"text": result.answer})
                return result

            thought = _extract_text(message["content"])
            if thought:
                self.on_event("thought", {"text": thought})

            tool_results = []
            for tool_use in tool_uses:
                name, tool_input = tool_use["name"], tool_use.get("input") or {}
                if isinstance(tool_input, str):  # some models send JSON as a string
                    try:
                        tool_input = json.loads(tool_input)
                    except json.JSONDecodeError:
                        tool_input = {}
                self.on_event("tool_call", {"name": name, "input": tool_input})
                output, is_error = execute_tool(self.db, name, tool_input)
                self.on_event("tool_result", {"name": name, "output": output, "is_error": is_error})
                result.tool_calls.append(ToolCall(name, tool_input, output, is_error))
                tool_result: dict[str, Any] = {
                    "toolUseId": tool_use["toolUseId"],
                    "content": [{"text": output}],
                }
                if is_error:
                    tool_result["status"] = "error"
                tool_results.append({"toolResult": tool_result})
            messages.append({"role": "user", "content": tool_results})

        result.answer = f"Stopped after {self.max_iterations} steps without a final answer."
        self.on_event("answer", {"text": result.answer})
        return result
