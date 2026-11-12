"""Sample SQLite database plus a safe, read-only query layer for the agent.

Schema (designed so interesting questions need multi-level joins):

    regions ─< employees ─< customers ─< orders ─< order_items >─ products >─ categories
                  │ (manager_id self-join)                           │
                  └──────────────────────────────────────────────────┴─< suppliers
"""

from __future__ import annotations

import random
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE regions (
    region_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    country     TEXT NOT NULL
);
CREATE TABLE employees (
    employee_id INTEGER PRIMARY KEY,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    title       TEXT NOT NULL,
    region_id   INTEGER NOT NULL REFERENCES regions(region_id),
    manager_id  INTEGER REFERENCES employees(employee_id),
    hire_date   TEXT NOT NULL
);
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL,
    city        TEXT NOT NULL,
    segment     TEXT NOT NULL CHECK (segment IN ('Consumer', 'Corporate', 'Small Business')),
    sales_rep_id INTEGER REFERENCES employees(employee_id),
    signup_date TEXT NOT NULL
);
CREATE TABLE suppliers (
    supplier_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    country     TEXT NOT NULL
);
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_category_id INTEGER REFERENCES categories(category_id)
);
CREATE TABLE products (
    product_id  INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(category_id),
    supplier_id INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    unit_price  REAL NOT NULL,
    unit_cost   REAL NOT NULL
);
CREATE TABLE orders (
    order_id    INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled'))
);
CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(order_id),
    product_id  INTEGER NOT NULL REFERENCES products(product_id),
    quantity    INTEGER NOT NULL,
    unit_price  REAL NOT NULL,
    discount    REAL NOT NULL DEFAULT 0
);
"""

_REGIONS = [(1, "North America", "USA"), (2, "Europe", "Germany"), (3, "Asia Pacific", "Singapore"), (4, "Latin America", "Brazil")]

_EMPLOYEES = [
    # id, first, last, title, region, manager, hire_date
    (1, "Grace", "Okafor", "VP of Sales", 1, None, "2015-03-01"),
    (2, "Lukas", "Brandt", "Regional Director", 2, 1, "2016-07-15"),
    (3, "Mei", "Tan", "Regional Director", 3, 1, "2017-01-09"),
    (4, "Diego", "Ramos", "Regional Director", 4, 1, "2018-05-21"),
    (5, "Aisha", "Bello", "Sales Rep", 1, 1, "2019-02-11"),
    (6, "Tom", "Walsh", "Sales Rep", 1, 1, "2020-08-03"),
    (7, "Sofia", "Klein", "Sales Rep", 2, 2, "2019-10-14"),
    (8, "Pierre", "Dubois", "Sales Rep", 2, 2, "2021-04-26"),
    (9, "Hiro", "Sato", "Sales Rep", 3, 3, "2020-01-06"),
    (10, "Priya", "Nair", "Sales Rep", 3, 3, "2022-06-13"),
    (11, "Camila", "Souza", "Sales Rep", 4, 4, "2021-09-01"),
]

_SUPPLIERS = [(1, "Acme Components", "USA"), (2, "Nordic Gear", "Sweden"), (3, "Shenzhen Electronics", "China"), (4, "Atlas Office Supply", "Mexico")]

_CATEGORIES = [
    (1, "Electronics", None), (2, "Office", None), (3, "Outdoor", None),
    (4, "Laptops", 1), (5, "Audio", 1), (6, "Accessories", 1),
    (7, "Furniture", 2), (8, "Stationery", 2), (9, "Camping", 3),
]

_PRODUCTS = [
    # id, name, category, supplier, price, cost
    (1, "UltraBook 14", 4, 3, 1299.00, 910.00),
    (2, "WorkStation Pro 16", 4, 1, 2199.00, 1650.00),
    (3, "NoiseFree Headphones", 5, 3, 249.00, 120.00),
    (4, "Studio Speakers", 5, 2, 399.00, 230.00),
    (5, "USB-C Hub", 6, 3, 59.00, 18.00),
    (6, "Wireless Mouse", 6, 1, 39.00, 12.00),
    (7, "Ergo Office Chair", 7, 4, 449.00, 260.00),
    (8, "Standing Desk", 7, 2, 699.00, 420.00),
    (9, "Premium Notebook Pack", 8, 4, 24.00, 7.00),
    (10, "Fountain Pen Set", 8, 2, 89.00, 35.00),
    (11, "Ultralight Tent", 9, 2, 379.00, 210.00),
    (12, "Trail Sleeping Bag", 9, 1, 159.00, 80.00),
]

_CITIES = {1: ["Seattle", "Austin", "Toronto"], 2: ["Berlin", "Paris", "Madrid"], 3: ["Singapore", "Tokyo", "Sydney"], 4: ["Sao Paulo", "Bogota", "Lima"]}
_REPS_BY_REGION = {1: [5, 6], 2: [7, 8], 3: [9, 10], 4: [11]}
_COMPANY_WORDS = ["Blue", "River", "Summit", "Nova", "Pixel", "Cedar", "Orbit", "Harbor", "Lumen", "Atlas", "Maple", "Quartz"]
_COMPANY_SUFFIX = ["Labs", "Group", "Studio", "Traders", "Works", "Partners"]


def create_sample_database(path: str | Path, seed: int = 42) -> Path:
    """Create (or recreate) the sample database at ``path`` with deterministic data."""
    path = Path(path)
    if path.exists():
        path.unlink()
    rng = random.Random(seed)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO regions VALUES (?,?,?)", _REGIONS)
        conn.executemany("INSERT INTO employees VALUES (?,?,?,?,?,?,?)", _EMPLOYEES)
        conn.executemany("INSERT INTO suppliers VALUES (?,?,?)", _SUPPLIERS)
        conn.executemany("INSERT INTO categories VALUES (?,?,?)", _CATEGORIES)
        conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", _PRODUCTS)

        customers = []
        for cid in range(1, 41):
            region = rng.choice(list(_CITIES))
            name = f"{rng.choice(_COMPANY_WORDS)} {rng.choice(_COMPANY_SUFFIX)} {cid}"
            email = name.lower().replace(" ", ".") + "@example.com"
            signup = date(2022, 1, 1) + timedelta(days=rng.randint(0, 700))
            customers.append((
                cid, name, email, rng.choice(_CITIES[region]),
                rng.choice(["Consumer", "Corporate", "Small Business"]),
                rng.choice(_REPS_BY_REGION[region]), signup.isoformat(),
            ))
        conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", customers)

        orders, items = [], []
        item_id = 1
        for oid in range(1, 301):
            cust = rng.choice(customers)
            signup = date.fromisoformat(cust[6])
            odate = signup + timedelta(days=rng.randint(1, 600))
            status = rng.choices(["delivered", "shipped", "pending", "cancelled"], weights=[70, 15, 8, 7])[0]
            orders.append((oid, cust[0], odate.isoformat(), status))
            for product in rng.sample(_PRODUCTS, rng.randint(1, 4)):
                discount = rng.choice([0, 0, 0, 0.05, 0.10, 0.15])
                items.append((item_id, oid, product[0], rng.randint(1, 5), product[4], discount))
                item_id += 1
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
        conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)
        conn.commit()
    finally:
        conn.close()
    return path


class QueryError(Exception):
    """Raised when a query is rejected or fails; the message is shown to the model."""


_READ_ONLY_START = re.compile(r"^\s*(SELECT|WITH|EXPLAIN|PRAGMA\s+table_info)\b", re.IGNORECASE)


class Database:
    """Read-only access to a SQLite database, safe to hand to an LLM agent."""

    def __init__(self, path: str | Path, max_rows: int = 200):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Database not found: {self.path}")
        self.max_rows = max_rows

    def _connect(self) -> sqlite3.Connection:
        # mode=ro makes SQLite itself refuse any write, independent of our checks.
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def list_tables(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]

    def describe_table(self, table: str) -> dict:
        if table not in self.list_tables():
            raise QueryError(f"Unknown table '{table}'. Available: {', '.join(self.list_tables())}")
        with self._connect() as conn:
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            fks = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
            sample = conn.execute(f'SELECT * FROM "{table}" LIMIT 3').fetchall()
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return {
            "table": table,
            "row_count": count,
            "columns": [
                {"name": c["name"], "type": c["type"], "primary_key": bool(c["pk"]), "not_null": bool(c["notnull"])}
                for c in cols
            ],
            "foreign_keys": [{"column": f["from"], "references": f"{f['table']}.{f['to']}"} for f in fks],
            "sample_rows": [dict(r) for r in sample],
        }

    def schema_overview(self) -> str:
        """Compact CREATE-style schema text, handy for the system prompt."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return "\n\n".join(r["sql"] for r in rows)

    def run_query(self, sql: str) -> dict:
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            raise QueryError("Empty query.")
        if ";" in sql:
            raise QueryError("Only a single SQL statement is allowed per call.")
        if not _READ_ONLY_START.match(sql):
            raise QueryError("Only read-only queries (SELECT / WITH) are allowed.")
        try:
            with self._connect() as conn:
                cur = conn.execute(sql)
                columns = [d[0] for d in cur.description or []]
                rows = cur.fetchmany(self.max_rows + 1)
        except sqlite3.Error as exc:
            raise QueryError(f"SQLite error: {exc}") from exc
        truncated = len(rows) > self.max_rows
        rows = rows[: self.max_rows]
        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }
