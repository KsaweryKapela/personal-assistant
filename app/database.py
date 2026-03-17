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
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytz

from app.config import DATABASE_URL, OPENAI_API_KEY, TIMEZONE

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "text-embedding-3-small"
_EMBEDDING_DIM = 1536
_VALID_STATUSES = {"completed", "completed_late", "skipped", "partial"}

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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_summaries (
                id                   SERIAL PRIMARY KEY,
                chat_id              BIGINT      NOT NULL,
                date                 DATE        NOT NULL,
                created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                wake_time            TIME,
                sleep_time           TIME,
                sleep_duration_hours NUMERIC(4,2),

                activities_completed INT         NOT NULL DEFAULT 0,
                activities_skipped   INT         NOT NULL DEFAULT 0,
                activities_partial   INT         NOT NULL DEFAULT 0,
                activities_total     INT         NOT NULL DEFAULT 0,
                completion_rate_pct  INT,

                workout_done         BOOLEAN,
                deep_work_minutes    INT,

                mood_score           INT         CHECK(mood_score    BETWEEN 1 AND 10),
                energy_score         INT         CHECK(energy_score  BETWEEN 1 AND 10),
                stress_score         INT         CHECK(stress_score  BETWEEN 1 AND 10),
                overall_score        INT         CHECK(overall_score BETWEEN 1 AND 10),

                highlights           TEXT        NOT NULL DEFAULT '',
                challenges           TEXT        NOT NULL DEFAULT '',
                summary              TEXT        NOT NULL DEFAULT '',

                metadata             JSONB       NOT NULL DEFAULT '{}',

                UNIQUE (chat_id, date)
            )
        """)
        # Migrate existing activities table — add time columns if not present
        cur.execute("ALTER TABLE activities ADD COLUMN IF NOT EXISTS start_time TIME")
        cur.execute("ALTER TABLE activities ADD COLUMN IF NOT EXISTS end_time TIME")

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


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def save_message(
    chat_id: int,
    role: str,
    content: str,
    message_type: str = "text",
) -> None:
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
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    if status not in _VALID_STATUSES:
        return {"ok": False, "error": f"Invalid status '{status}'. Use: {sorted(_VALID_STATUSES)}"}

    embed_text = f"{name} ({category}) — {status}" + (f" — {notes}" if notes else "")
    embedding = _embed(embed_text)

    with _conn() as c:
        cur = c.cursor()
        cur.execute(
            """
            INSERT INTO activities
                (chat_id, category, name, status, notes, metadata, embedding, start_time, end_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING timestamp
            """,
            (
                chat_id, category, name, status, notes,
                json.dumps(metadata or {}), embedding,
                start_time, end_time,
            ),
        )
        ts = cur.fetchone()[0].isoformat()

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


def update_activity(
    chat_id: int,
    activity_id: int,
    category: str | None = None,
    name: str | None = None,
    status: str | None = None,
    notes: str | None = None,
    metadata: dict | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    """Update fields of an existing activity record (scoped to chat_id for safety)."""
    if status is not None and status not in _VALID_STATUSES:
        return {"ok": False, "error": f"Invalid status '{status}'. Use: {sorted(_VALID_STATUSES)}"}

    updates = {}
    if category is not None:
        updates["category"] = category
    if name is not None:
        updates["name"] = name
    if status is not None:
        updates["status"] = status
    if notes is not None:
        updates["notes"] = notes
    if metadata is not None:
        updates["metadata"] = json.dumps(metadata)
    if start_time is not None:
        updates["start_time"] = start_time
    if end_time is not None:
        updates["end_time"] = end_time

    if not updates:
        return {"ok": False, "error": "No fields provided to update."}

    set_clause = ", ".join(f"{col} = %s" for col in updates)
    values = list(updates.values()) + [activity_id, chat_id]

    with _conn() as c:
        cur = c.cursor()
        cur.execute(
            f"UPDATE activities SET {set_clause} WHERE id = %s AND chat_id = %s",
            values,
        )
        updated = cur.rowcount

    if updated:
        return {"ok": True, "updated_id": activity_id, "fields_changed": list(updates)}
    return {"ok": False, "error": f"No activity with id={activity_id} found for this user."}


def delete_activity(chat_id: int, activity_id: int) -> dict:
    """Delete a single activity record by ID (scoped to chat_id for safety)."""
    with _conn() as c:
        cur = c.cursor()
        cur.execute(
            "DELETE FROM activities WHERE id = %s AND chat_id = %s",
            (activity_id, chat_id),
        )
        deleted = cur.rowcount
    if deleted:
        return {"ok": True, "deleted_id": activity_id}
    return {"ok": False, "error": f"No activity with id={activity_id} found for this user."}


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

    return {
        "query": query,
        "results": [
            {
                "source": r["source"],
                "text": r["text"],
                "timestamp": r["timestamp"].isoformat(),
                "extra": r["extra"],
                "relevance_score": round(1 - r["distance"], 4),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------

def save_daily_summary(
    chat_id: int,
    date: str,
    wake_time: str | None = None,
    sleep_time: str | None = None,
    sleep_duration_hours: float | None = None,
    activities_completed: int = 0,
    activities_skipped: int = 0,
    activities_partial: int = 0,
    activities_total: int = 0,
    completion_rate_pct: int | None = None,
    workout_done: bool | None = None,
    deep_work_minutes: int | None = None,
    mood_score: int | None = None,
    energy_score: int | None = None,
    stress_score: int | None = None,
    overall_score: int | None = None,
    highlights: str = "",
    challenges: str = "",
    summary: str = "",
    metadata: dict | None = None,
) -> dict:
    """Upsert a daily summary record (one per chat_id + date)."""
    with _conn() as c:
        cur = c.cursor()
        cur.execute(
            """
            INSERT INTO daily_summaries (
                chat_id, date,
                wake_time, sleep_time, sleep_duration_hours,
                activities_completed, activities_skipped, activities_partial, activities_total,
                completion_rate_pct, workout_done, deep_work_minutes,
                mood_score, energy_score, stress_score, overall_score,
                highlights, challenges, summary, metadata
            ) VALUES (
                %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (chat_id, date) DO UPDATE SET
                wake_time            = EXCLUDED.wake_time,
                sleep_time           = EXCLUDED.sleep_time,
                sleep_duration_hours = EXCLUDED.sleep_duration_hours,
                activities_completed = EXCLUDED.activities_completed,
                activities_skipped   = EXCLUDED.activities_skipped,
                activities_partial   = EXCLUDED.activities_partial,
                activities_total     = EXCLUDED.activities_total,
                completion_rate_pct  = EXCLUDED.completion_rate_pct,
                workout_done         = EXCLUDED.workout_done,
                deep_work_minutes    = EXCLUDED.deep_work_minutes,
                mood_score           = EXCLUDED.mood_score,
                energy_score         = EXCLUDED.energy_score,
                stress_score         = EXCLUDED.stress_score,
                overall_score        = EXCLUDED.overall_score,
                highlights           = EXCLUDED.highlights,
                challenges           = EXCLUDED.challenges,
                summary              = EXCLUDED.summary,
                metadata             = EXCLUDED.metadata,
                created_at           = NOW()
            """,
            (
                chat_id, date,
                wake_time, sleep_time, sleep_duration_hours,
                activities_completed, activities_skipped, activities_partial, activities_total,
                completion_rate_pct, workout_done, deep_work_minutes,
                mood_score, energy_score, stress_score, overall_score,
                highlights, challenges, summary,
                json.dumps(metadata or {}),
            ),
        )
    return {"ok": True, "date": date}


# ---------------------------------------------------------------------------
# Generic read-only query
# ---------------------------------------------------------------------------

def run_query(sql: str) -> dict:
    """Execute a SELECT query and return rows. Rejects non-SELECT statements."""
    if not sql.strip().upper().startswith("SELECT"):
        return {"error": "Only SELECT queries are allowed."}
    with _conn() as c:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            return {
                "rows": [dict(r) for r in rows],
                "count": len(rows),
            }
        except Exception as exc:
            return {"error": str(exc)}
