"""Command-line interface.

    python -m sql_agent init-db
    python -m sql_agent ask "Which category made the most revenue?" --model gpt
    python -m sql_agent compare "Top 3 sales reps by revenue" --models qwen gpt
    python -m sql_agent demo --model qwen
    python -m sql_agent chat --model qwen        # type /model gpt to switch mid-chat
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import SQLAgent
from .config import DEFAULT_MODEL, MODELS
from .database import Database, create_sample_database

DEFAULT_DB = Path("sample.db")

# Questions of increasing difficulty: single table -> joins -> multi-level joins.
DEMO_QUESTIONS = [
    ("simple", "How many customers do we have in each segment?"),
    ("join", "List the 5 customers with the most orders, with their order counts."),
    ("join", "What is the total revenue per product category (leaf categories)?"),
    ("multi-level join", "Which sales rep generated the most revenue from delivered orders, and which region are they in?"),
    ("multi-level join", "For each region, what is the top-selling top-level category (Electronics/Office/Outdoor) by revenue?"),
    ("multi-level join", "Which supplier's products earned the highest gross profit, and who is the regional director over the reps who sold the most of them?"),
]


def _printer(verbose: bool):
    def on_event(kind: str, data: dict) -> None:
        if not verbose:
            return
        if kind == "thought":
            print(f"  💭 {data['text']}")
        elif kind == "tool_call":
            if data["name"] == "run_sql":
                print(f"  🛠  run_sql:\n{_indent(data['input'].get('sql', ''), 6)}")
            else:
                print(f"  🛠  {data['name']}({json.dumps(data['input'])})")
        elif kind == "tool_result":
            marker = "❌" if data["is_error"] else "✅"
            out = data["output"]
            print(f"  {marker} {out[:300]}{'…' if len(out) > 300 else ''}")
    return on_event


def _indent(text: str, n: int) -> str:
    return "\n".join(" " * n + line for line in text.strip().splitlines())


def _ensure_db(path: Path) -> Database:
    if not path.exists():
        print(f"Creating sample database at {path} ...")
        create_sample_database(path)
    return Database(path)


def _print_result(result) -> None:
    print(f"\n🤖 [{result.model_id}]\n{result.answer}")
    print(f"\n   steps={result.iterations} sql_queries={len(result.sql_queries)} "
          f"tokens in/out={result.input_tokens}/{result.output_tokens}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sql_agent", description="Agentic text-to-SQL on Amazon Bedrock")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path (default: sample.db)")
    parser.add_argument("--region", default=None, help="AWS region (default: $AWS_REGION or us-west-2)")
    parser.add_argument("-q", "--quiet", action="store_true", help="Hide the agent's tool calls")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="(Re)create the sample database")
    sub.add_parser("models", help="List model aliases")

    p_ask = sub.add_parser("ask", help="Ask a single question")
    p_ask.add_argument("question")
    p_ask.add_argument("--model", default=DEFAULT_MODEL, help=f"Alias ({', '.join(MODELS)}) or Bedrock model ID")

    p_cmp = sub.add_parser("compare", help="Ask the same question to several models")
    p_cmp.add_argument("question")
    p_cmp.add_argument("--models", nargs="+", default=["qwen", "gpt"])

    p_demo = sub.add_parser("demo", help="Run demo questions from simple to multi-level joins")
    p_demo.add_argument("--model", default=DEFAULT_MODEL)

    p_chat = sub.add_parser("chat", help="Interactive chat (/model <name> to switch, /reset, /quit)")
    p_chat.add_argument("--model", default=DEFAULT_MODEL)

    args = parser.parse_args(argv)
    verbose = not args.quiet

    if args.command == "init-db":
        create_sample_database(args.db)
        print(f"Created {args.db}")
        return 0
    if args.command == "models":
        for alias, model_id in MODELS.items():
            print(f"{alias:12s} {model_id}")
        return 0

    db = _ensure_db(args.db)

    if args.command == "ask":
        agent = SQLAgent(db, model=args.model, region=args.region, on_event=_printer(verbose))
        print(f"❓ {args.question}")
        _print_result(agent.ask(args.question))

    elif args.command == "compare":
        agent = SQLAgent(db, model=args.models[0], region=args.region, on_event=_printer(verbose))
        for model in args.models:
            agent.set_model(model)
            print(f"\n{'=' * 70}\n❓ {args.question}\n   model: {agent.model_id}")
            _print_result(agent.ask(args.question))

    elif args.command == "demo":
        agent = SQLAgent(db, model=args.model, region=args.region, on_event=_printer(verbose))
        for level, question in DEMO_QUESTIONS:
            print(f"\n{'=' * 70}\n[{level}] ❓ {question}")
            _print_result(agent.ask(question))

    elif args.command == "chat":
        agent = SQLAgent(db, model=args.model, region=args.region, on_event=_printer(verbose))
        history: list = []
        print(f"Chatting with {agent.model_id}. Commands: /model <alias|id>, /reset, /quit")
        while True:
            try:
                line = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line == "/reset":
                history = []
                print("History cleared.")
                continue
            if line.startswith("/model"):
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    agent.set_model(parts[1])
                    # Different models can't always read each other's reasoning blocks,
                    # so start a fresh conversation after switching.
                    history = []
                print(f"Model: {agent.model_id}")
                continue
            _print_result(agent.ask(line, history=history))
    return 0


if __name__ == "__main__":
    sys.exit(main())
