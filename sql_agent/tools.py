"""Tools the agent can call, expressed as Bedrock Converse ``toolSpec`` definitions."""

from __future__ import annotations

import json
from typing import Any

from .database import Database, QueryError

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "toolSpec": {
            "name": "list_tables",
            "description": "List all tables in the database.",
            "inputSchema": {"json": {"type": "object", "properties": {}, "required": []}},
        }
    },
    {
        "toolSpec": {
            "name": "describe_table",
            "description": (
                "Get the columns, types, primary key, foreign keys (join paths), row count "
                "and a few sample rows of one table. Use this before writing joins."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"table": {"type": "string", "description": "Exact table name."}},
                    "required": ["table"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "run_sql",
            "description": (
                "Execute ONE read-only SQLite query (SELECT or WITH) and return the result rows. "
                "If it fails, the error message is returned so you can fix the query and retry."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "A single SQLite SELECT/WITH statement."},
                        "reasoning": {
                            "type": "string",
                            "description": "One sentence on what this query computes and why.",
                        },
                    },
                    "required": ["sql"],
                }
            },
        }
    },
]


def execute_tool(db: Database, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
    """Run a tool. Returns (result_text, is_error)."""
    try:
        if name == "list_tables":
            result: Any = {"tables": db.list_tables()}
        elif name == "describe_table":
            result = db.describe_table(str(tool_input.get("table", "")))
        elif name == "run_sql":
            result = db.run_query(str(tool_input.get("sql", "")))
        else:
            return f"Unknown tool '{name}'.", True
    except QueryError as exc:
        return str(exc), True
    return json.dumps(result, default=str), False
