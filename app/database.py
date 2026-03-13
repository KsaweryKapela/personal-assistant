"""
PostgreSQL + pgvector database layer.

Tables:
    activities  — logged user activities with embeddings for RAG
    messages    — every user/assistant message with embeddings for RAG
    profile     — user profile JSON (primary source, env is fallback on first run)

Railway setup:
    1. Add a PostgreSQL service to your Railway project
    2. DATABASE_URL is auto-injected — no manual config needed
    3. pgvector is pre-installed on Railway's Postgres
"""

import json
import logging
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytz

from app.config import DATABASE_URL, OPENAI_API_KEY, TIMEZONE

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "text-embedding-3-small"
_EMBEDDING_DIM = 1536
_VALID_STATUSES = {"completed", "completed_late", "skipped", "partial"}


def _serialize_row(row: dict) -> dict:
    """Convert a Postgres row dict to JSON-safe types."""
    result = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            result[k] = v.isoformat()
        elif isinstance(v, Decimal):
            result[k] = float(v)
        elif isinstance(v, memoryview):
            result[k] = bytes(v).hex()
        else:
            result[k] = v
    return result

_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
                logger.info("DB connection pool created")
    return _pool


def _conn():
    """Context manager that borrows a connection from the pool."""
    class _Ctx:
        def __enter__(self):
            self.c = _get_pool().getconn()
            self.c.autocommit = False
            return self.c
        def __exit__(self, exc_type, *_):
            if exc_type:
                self.c.rollback()
            else:
                self.c.commit()
            _get_pool().putconn(self.c)
    return _Ctx()


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def _embed(text: str) -> list[float]:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.embeddings.create(
        model=_EMBEDDING_MODEL,
        input=text[:8000],
    )
    return response.data[0].embedding


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create tables + pgvector extension, then log current state."""
    with _conn() as c:
        cur = c.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS activities (
                id          SERIAL PRIMARY KEY,
                chat_id     BIGINT      NOT NULL,
                timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                category    TEXT        NOT NULL,
                name        TEXT        NOT NULL,
                status      TEXT        NOT NULL
                                CHECK(status IN ('completed','completed_late','skipped','partial')),
                notes       TEXT        NOT NULL DEFAULT '',
                metadata    JSONB       NOT NULL DEFAULT '{{}}',
                embedding   vector({_EMBEDDING_DIM})
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS messages (
                id           SERIAL PRIMARY KEY,
                chat_id      BIGINT      NOT NULL,
                timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                role         TEXT        NOT NULL CHECK(role IN ('user','assistant')),
                content      TEXT        NOT NULL,
                message_type TEXT        NOT NULL DEFAULT 'text'
                                 CHECK(message_type IN ('text','voice')),
                embedding    vector({_EMBEDDING_DIM})
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS profile (
                chat_id    BIGINT PRIMARY KEY,
                data       JSONB       NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        # Indexes for fast vector search
        cur.execute("""
            CREATE INDEX IF NOT EXISTS activities_embedding_idx
            ON activities USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 10)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS messages_embedding_idx
            ON messages USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 10)
        """)

    logger.info("DB init complete | url=%s", DATABASE_URL[:40] + "...")
    _log_state()


def _log_state() -> None:
    with _conn() as c:
        cur = c.cursor()
        for table in ("activities", "messages", "profile"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            logger.info("DB state | %s: %d rows", table, count)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def load_profile_from_db(chat_id: int) -> dict | None:
    with _conn() as c:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT data FROM profile WHERE chat_id = %s", (chat_id,))
        row = cur.fetchone()
    return dict(row["data"]) if row else None


def save_profile_to_db(chat_id: int, profile: dict) -> None:
    with _conn() as c:
        cur = c.cursor()
        cur.execute(
            """
            INSERT INTO profile (chat_id, data, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE
                SET data = EXCLUDED.data, updated_at = NOW()
            """,
            (chat_id, json.dumps(profile, ensure_ascii=False)),
        )
    logger.info("DB profile saved | chat_id=%s", chat_id)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def save_message(
    chat_id: int,
    role: str,
    content: str,
    message_type: str = "text",
) -> None:
    logger.info("DB save_message | chat_id=%s | role=%s | type=%s | len=%d", chat_id, role, message_type, len(content))
    embedding = _embed(content)
    with _conn() as c:
        cur = c.cursor()
        cur.execute(
            """
            INSERT INTO messages (chat_id, role, content, message_type, embedding)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (chat_id, role, content, message_type, embedding),
        )
    logger.info("DB save_message | ok | chat_id=%s | role=%s", chat_id, role)


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

def log_activity(
    chat_id: int,
    category: str,
    name: str,
    status: str,
    notes: str = "",
    metadata: dict | None = None,
) -> dict:
    if status not in _VALID_STATUSES:
        return {"ok": False, "error": f"Invalid status '{status}'. Use: {sorted(_VALID_STATUSES)}"}

    logger.info("DB log_activity | chat_id=%s | category=%s | name=%s | status=%s", chat_id, category, name, status)
    embed_text = f"{name} ({category}) — {status}" + (f" — {notes}" if notes else "")
    embedding = _embed(embed_text)

    with _conn() as c:
        cur = c.cursor()
        cur.execute(
            """
            INSERT INTO activities
                (chat_id, category, name, status, notes, metadata, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING timestamp
            """,
            (
                chat_id, category, name, status, notes,
                json.dumps(metadata or {}), embedding,
            ),
        )
        ts = cur.fetchone()[0].isoformat()

    logger.info("Activity logged | %s | %s | %s", category, name, status)
    return {"ok": True, "timestamp": ts}


def query_stats(
    chat_id: int,
    category: str | None = None,
    period_days: int = 7,
) -> dict:
    tz = pytz.timezone(TIMEZONE)
    since = datetime.now(tz) - timedelta(days=period_days)

    where = "WHERE chat_id = %s AND timestamp >= %s"
    params: list = [chat_id, since]
    if category:
        where += " AND category = %s"
        params.append(category)

    with _conn() as c:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"SELECT category, status, COUNT(*) AS cnt "
            f"FROM activities {where} GROUP BY category, status",
            params,
        )
        status_rows = cur.fetchall()
        cur.execute(
            f"SELECT timestamp, category, name, status, notes "
            f"FROM activities {where} ORDER BY timestamp DESC LIMIT 20",
            params,
        )
        recent_rows = cur.fetchall()

    stats: dict = {}
    for row in status_rows:
        cat = row["category"]
        if cat not in stats:
            stats[cat] = {"completed": 0, "completed_late": 0, "skipped": 0, "partial": 0, "total": 0}
        stats[cat][row["status"]] = stats[cat].get(row["status"], 0) + row["cnt"]
        stats[cat]["total"] += row["cnt"]

    for cat in stats:
        total = stats[cat]["total"]
        done = stats[cat]["completed"] + stats[cat]["completed_late"]
        stats[cat]["completion_rate_pct"] = round(done / total * 100) if total else 0
        stats[cat]["on_time_rate_pct"] = round(stats[cat]["completed"] / total * 100) if total else 0

    return {
        "period_days": period_days,
        "stats_by_category": stats,
        "recent": [
            {
                "timestamp": r["timestamp"].isoformat(),
                "category": r["category"],
                "name": r["name"],
                "status": r["status"],
                "notes": r["notes"],
            }
            for r in recent_rows
        ],
    }


def get_recent_activities(chat_id: int, limit: int = 10) -> list[dict]:
    """For system prompt context injection."""
    with _conn() as c:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT timestamp, category, name, status "
            "FROM activities WHERE chat_id = %s ORDER BY timestamp DESC LIMIT %s",
            (chat_id, limit),
        )
        rows = cur.fetchall()
    return [
        {**dict(r), "timestamp": r["timestamp"].isoformat()}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Semantic search (RAG)
# ---------------------------------------------------------------------------

def search_memory(
    chat_id: int,
    query: str,
    limit: int = 5,
) -> dict:
    """Semantic search across messages and activities using cosine similarity."""
    logger.info("DB search_memory | chat_id=%s | query=%r | limit=%d", chat_id, query[:100], limit)
    embedding = _embed(query)

    with _conn() as c:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""
            SELECT 'message' AS source,
                   content   AS text,
                   timestamp,
                   role      AS extra,
                   embedding <=> %s::vector AS distance
            FROM messages
            WHERE chat_id = %s AND embedding IS NOT NULL
            UNION ALL
            SELECT 'activity'                                          AS source,
                   name || ' (' || category || ', ' || status || ')' AS text,
                   timestamp,
                   notes                                               AS extra,
                   embedding <=> %s::vector                           AS distance
            FROM activities
            WHERE chat_id = %s AND embedding IS NOT NULL
            ORDER BY distance ASC
            LIMIT %s
            """,
            (embedding, chat_id, embedding, chat_id, limit),
        )
        rows = cur.fetchall()

    results = [
        {
            "source": r["source"],
            "text": r["text"],
            "timestamp": r["timestamp"].isoformat() if hasattr(r["timestamp"], "isoformat") else r["timestamp"],
            "extra": r["extra"],
            "relevance_score": round(1 - float(r["distance"]), 4),
        }
        for r in rows
    ]
    logger.info("DB search_memory | ok | results=%d", len(results))
    return {"query": query, "results": results}


# ---------------------------------------------------------------------------
# Generic read-only query
# ---------------------------------------------------------------------------

def run_query(sql: str) -> dict:
    """Execute a SELECT query and return rows. Rejects non-SELECT statements."""
    if not sql.strip().upper().startswith("SELECT"):
        return {"error": "Only SELECT queries are allowed."}
    logger.info("DB run_query | sql=%r", sql[:200])
    with _conn() as c:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            result = [_serialize_row(dict(r)) for r in rows]
            logger.info("DB run_query | ok | rows=%d", len(result))
            return {
                "rows": result,
                "count": len(result),
            }
        except Exception as exc:
            return {"error": str(exc)}
