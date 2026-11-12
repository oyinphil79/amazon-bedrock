# Agentic Text-to-SQL on Amazon Bedrock

An agent that turns natural-language questions into SQL, runs them against a database,
and answers back in plain English, built on the **Amazon Bedrock Converse API**.

What it covers:
- **Switching models** (Qwen or GPT) with one flag, or mid-chat with `/model gpt`.
- **Letting the agent use the database**: it explores the schema, runs read-only SQL,
  reads the results (or the error), fixes its query, and answers.
- **Complex joins**: the sample schema needs 4–6-table and self-joins for the harder questions.

```
user question ─▶ model on Bedrock ──toolUse──▶ list_tables / describe_table / run_sql ──▶ SQLite (read-only)
                       ▲                                                                    │
                       └──────────────────────── toolResult (rows or error) ◀───────────────┘
                 stopReason = end_turn ─▶ natural-language answer
```

## Project layout

| File | Purpose |
|---|---|
| `sql_agent/config.py` | Model aliases → Bedrock model IDs (`qwen`, `qwen-large`, `qwen-coder`, `gpt`, `gpt-small`) |
| `sql_agent/agent.py` | The agent loop (`client.converse(...)` + tool execution) |
| `sql_agent/tools.py` | Tool definitions (`toolSpec`) and dispatcher |
| `sql_agent/database.py` | Sample DB generator + read-only, single-statement, row-limited query layer |
| `sql_agent/cli.py` | `ask`, `compare`, `demo`, `chat` commands |
| `tests/` | Offline tests using a fake Bedrock client |

## Sample database

```
regions ─< employees ─< customers ─< orders ─< order_items >─ products >─ categories (parent_category_id)
              └─ manager_id (self-join)                          └─ suppliers
```
8 tables, 40 customers, 300 orders, ~750 order lines. Revenue = `quantity * unit_price * (1 - discount)`.

## Setup

1. **Enable model access.** In the Bedrock console go to *Model access* and enable the Qwen3
   and OpenAI gpt-oss models in your region. `eu-north-1` is the default here. Confirm the exact IDs:
   ```bash
   aws bedrock list-foundation-models --region eu-north-1 --query "modelSummaries[?contains(modelId,'qwen') || contains(modelId,'gpt-oss')].modelId"
   ```
   If your IDs differ, edit `MODELS` in `sql_agent/config.py`. You can also pass any Bedrock model ID
   or inference profile directly with `--model`.
2. **Credentials.** Any standard boto3 credentials (`aws configure`, SSO, env vars, IAM role).
   They need `bedrock:InvokeModel` on the models you use.
3. **Install:**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   ```
   ```bash
   pip install -r requirements-dev.txt
   ```

## Usage

```bash
python -m sql_agent init-db
```
```bash
python -m sql_agent ask "How many customers are in each segment?" --model qwen
```
```bash
python -m sql_agent ask "Which sales rep generated the most revenue from delivered orders, and in which region?" --model gpt
```
```bash
python -m sql_agent compare "For each region, what is the top-selling top-level category by revenue?" --models qwen gpt
```
```bash
python -m sql_agent demo --model qwen
```
```bash
python -m sql_agent chat --model qwen
```
In `chat`, follow-up questions keep context. Type `/model gpt` to switch models, `/reset` to clear history, and `/quit` to exit.
Use `-q` to hide the agent's tool calls, `--region` to choose a region, and `--db` to use a different SQLite file.

By default you see each step: the SQL generated, the rows returned, any errors, and the agent retrying.

### Use it from Python

```python
from sql_agent import SQLAgent
from sql_agent.database import Database

agent = SQLAgent(Database("sample.db"), model="qwen")
result = agent.ask("Top 3 products by gross profit?")
print(result.answer, result.sql_queries)

agent.set_model("gpt")           # switch models
print(agent.ask("Same question, but only for the Europe region").answer)
```

## Simple vs. multi-level join questions

`demo` runs these questions in order of difficulty:

| Level | Question | Joins needed |
|---|---|---|
| simple | Customers per segment | 1 table |
| join | Top 5 customers by order count | customers–orders |
| join | Revenue per leaf category | order_items–products–categories |
| multi-level | Top rep by delivered revenue and their region | order_items–orders–customers–employees–regions |
| multi-level | Top top-level category per region | 6 tables, a category self-join, and ranking per group |
| multi-level | Most profitable supplier, and the director over its top reps | suppliers…employees with a manager self-join |

Things to watch when comparing models on the harder questions:
- Does it call `describe_table` to find the foreign keys, or guess column names?
- Does it fix its query after a SQLite error (the error is returned as a `toolResult` with `status: error`)?
- Does it join through the category hierarchy and the `manager_id` self-join correctly?
- Steps and token counts are printed after every answer.

## Safety

The agent can only read data. The database is opened with SQLite `mode=ro`, only one
`SELECT`/`WITH` statement is allowed per call, and results are capped at 200 rows.
To use a production database, give the agent a read-only database user as well.

## Tests

```bash
python -m pytest
```
The tests use a scripted fake Bedrock client, so no AWS credentials are needed. They cover the
tool loop, retrying after an error, switching models, stripping reasoning text, and blocking writes.
