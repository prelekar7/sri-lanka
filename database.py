"""
database.py — SQLite database layer for Sri Lanka Price Data.
Handles schema creation, inserts, and analytical queries.
"""

import sqlite3
import os
from datetime import datetime, date
from typing import Optional

DB_PATH = os.environ.get(
    "PRICES_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "prices.db"),
)
DATABASE_URL = os.environ.get("DATABASE_URL")


class PostgresConnection:
    """Small adapter so existing query helpers can use either backend."""

    def __init__(self, connection):
        self._connection = connection

    @staticmethod
    def _query(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, params=()):
        return self._connection.execute(self._query(sql), params)

    def executemany(self, sql: str, rows):
        return self._connection.cursor().executemany(self._query(sql), rows)

    def executescript(self, sql: str):
        for statement in sql.split(";"):
            if statement.strip():
                self._connection.execute(statement)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def get_connection():
    """Get a database connection, creating the DB if needed."""
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row

        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        return PostgresConnection(psycopg.connect(url, row_factory=dict_row))
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    schema = """
        CREATE TABLE IF NOT EXISTS price_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date DATE UNIQUE NOT NULL,
            pdf_url TEXT NOT NULL,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date DATE NOT NULL,
            category TEXT NOT NULL,        -- VEGETABLES, OTHER, FRUITS, FISH, RICE
            item_name TEXT NOT NULL,
            unit TEXT NOT NULL,             -- Rs./kg, Rs./Nut, Rs./Each, Rs./Ltr
            market TEXT NOT NULL,           -- Pettah, Dambulla, Narahenpita
            price_type TEXT NOT NULL,       -- wholesale, retail
            yesterday_price REAL,
            today_price REAL,
            FOREIGN KEY (report_date) REFERENCES price_reports(report_date),
            UNIQUE(report_date, item_name, market, price_type)
        );

        CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(report_date);
        CREATE INDEX IF NOT EXISTS idx_prices_item ON prices(item_name);
        CREATE INDEX IF NOT EXISTS idx_prices_category ON prices(category);
        CREATE INDEX IF NOT EXISTS idx_prices_market ON prices(market);
    """
    if DATABASE_URL:
        schema = schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    conn.executescript(schema)
    conn.commit()
    conn.close()


def report_exists(report_date: date) -> bool:
    """Check if a report for this date is already in the DB."""
    conn = get_connection()
    cur = conn.execute(
        "SELECT 1 FROM price_reports WHERE report_date = ?",
        (report_date,)
    )
    exists = cur.fetchone() is not None
    conn.close()
    return exists


def insert_report(report_date: date, pdf_url: str, prices: list[dict]):
    """
    Insert a full report's data.
    prices: list of dicts with keys:
        category, item_name, unit, market, price_type, yesterday_price, today_price
    """
    conn = get_connection()
    try:
        if DATABASE_URL:
            conn.execute(
                """INSERT INTO price_reports (report_date, pdf_url) VALUES (?, ?)
                   ON CONFLICT (report_date) DO UPDATE SET pdf_url = EXCLUDED.pdf_url""",
                (report_date, pdf_url),
            )
            insert_sql = """INSERT INTO prices
               (report_date, category, item_name, unit, market, price_type, yesterday_price, today_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (report_date, item_name, market, price_type) DO UPDATE SET
                 category = EXCLUDED.category, unit = EXCLUDED.unit,
                 yesterday_price = EXCLUDED.yesterday_price, today_price = EXCLUDED.today_price"""
        else:
            conn.execute(
                "INSERT OR IGNORE INTO price_reports (report_date, pdf_url) VALUES (?, ?)",
                (report_date, pdf_url)
            )
            insert_sql = """INSERT OR REPLACE INTO prices
               (report_date, category, item_name, unit, market, price_type, yesterday_price, today_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
        conn.executemany(
            insert_sql,
            [
                (
                    report_date,
                    p['category'],
                    p['item_name'],
                    p['unit'],
                    p['market'],
                    p['price_type'],
                    p.get('yesterday_price'),
                    p.get('today_price'),
                )
                for p in prices
            ]
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def get_all_items() -> list[str]:
    """Get all unique item names."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT item_name FROM prices ORDER BY item_name"
    ).fetchall()
    conn.close()
    return [r['item_name'] for r in rows]


def get_all_categories() -> list[str]:
    """Get all unique categories."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT category FROM prices ORDER BY category"
    ).fetchall()
    conn.close()
    return [r['category'] for r in rows]


def get_all_markets() -> list[str]:
    """Get all unique markets."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT market FROM prices ORDER BY market"
    ).fetchall()
    conn.close()
    return [r['market'] for r in rows]


def get_date_range() -> tuple[Optional[date], Optional[date]]:
    """Get the earliest and latest report dates in DB."""
    conn = get_connection()
    row = conn.execute(
        "SELECT MIN(report_date) as min_d, MAX(report_date) as max_d FROM price_reports"
    ).fetchone()
    conn.close()
    if row and row['min_d']:
        return (
            date.fromisoformat(str(row['min_d'])),
            date.fromisoformat(str(row['max_d'])),
        )
    return (None, None)


def get_report_count() -> int:
    """Get total number of scraped reports."""
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM price_reports").fetchone()
    conn.close()
    return row['cnt']


def get_latest_prices() -> list[dict]:
    """Get prices from the most recent report."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT p.*, pr.report_date
        FROM prices p
        JOIN price_reports pr ON p.report_date = pr.report_date
        WHERE pr.report_date = (SELECT MAX(report_date) FROM price_reports)
        ORDER BY p.category, p.item_name, p.market, p.price_type
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_price_history(
    item_name: str,
    market: Optional[str] = None,
    price_type: str = 'retail',
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[dict]:
    """Get historical prices for an item."""
    query = """
        SELECT report_date, item_name, market, price_type, today_price, yesterday_price
        FROM prices
        WHERE item_name = ? AND price_type = ?
    """
    params: list = [item_name, price_type]

    if market:
        query += " AND market = ?"
        params.append(market)

    if start_date:
        query += " AND report_date >= ?"
        params.append(start_date)

    if end_date:
        query += " AND report_date <= ?"
        params.append(end_date)

    query += " ORDER BY report_date ASC"

    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_multi_item_history(
    item_names: list[str],
    market: str,
    price_type: str = 'retail',
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[dict]:
    """Get historical prices for multiple items (for comparison charts)."""
    placeholders = ','.join('?' * len(item_names))
    query = f"""
        SELECT report_date, item_name, market, price_type, today_price
        FROM prices
        WHERE item_name IN ({placeholders}) AND market = ? AND price_type = ?
    """
    params: list = list(item_names) + [market, price_type]

    if start_date:
        query += " AND report_date >= ?"
        params.append(start_date)

    if end_date:
        query += " AND report_date <= ?"
        params.append(end_date)

    query += " ORDER BY report_date ASC, item_name"

    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_items_by_category(category: str) -> list[str]:
    """Get all items in a given category."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT item_name FROM prices WHERE category = ? ORDER BY item_name",
        (category,)
    ).fetchall()
    conn.close()
    return [r['item_name'] for r in rows]


def get_price_stats(
    item_name: str,
    market: str,
    price_type: str = 'retail',
    days: int = 30,
) -> dict:
    """Get price statistics for an item over the last N days."""
    from datetime import timedelta

    conn = get_connection()
    cutoff = date.today() - timedelta(days=days)
    row = conn.execute("""
        SELECT 
            AVG(today_price) as avg_price,
            MIN(today_price) as min_price,
            MAX(today_price) as max_price,
            COUNT(today_price) as data_points
        FROM prices
        WHERE item_name = ? AND market = ? AND price_type = ?
          AND report_date >= ?
          AND today_price IS NOT NULL
    """, (item_name, market, price_type, cutoff)).fetchone()
    conn.close()
    return dict(row) if row else {}


# Initialize on import
init_db()
