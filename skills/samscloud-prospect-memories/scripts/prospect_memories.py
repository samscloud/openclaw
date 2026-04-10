"""
Samscloud Prospect Memory Directory
====================================
Structured per-prospect memory storage for the OpenClaw agent.

Stores and retrieves typed facts about individual prospects:
- objections, preferences, signals, relationship context, next steps, blockers

Storage: SQLite at /root/.openclaw/memory/prospect_memories.db

Usage:
    import sys
    sys.path.insert(0, "/root/.openclaw/skills/samscloud-prospect-memories/scripts")
    import prospect_memories as pm

    pm.store(prospect_context_id="uuid", memory_type="objection", content="...")
    memories = pm.get(prospect_context_id="uuid")
    summary = pm.summarize(prospect_context_id="uuid")
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MEMORY_DIR = Path(os.environ.get("OPENCLAW_STATE_DIR", Path.home() / ".openclaw")) / "memory"
DB_PATH = MEMORY_DIR / "prospect_memories.db"

VALID_MEMORY_TYPES = {"objection", "preference", "signal", "relationship", "next_step", "blocker"}

# Default relevance decay thresholds (days before a memory is considered stale)
DECAY_THRESHOLDS = {
    "objection": 180,
    "preference": 365,
    "signal": 60,
    "relationship": 365,
    "next_step": 14,
    "blocker": 90,
}


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """Get a database connection, creating the DB and tables if needed."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prospect_memories (
            id TEXT PRIMARY KEY,
            prospect_context_id TEXT NOT NULL,
            memory_type TEXT NOT NULL CHECK(memory_type IN ('objection','preference','signal','relationship','next_step','blocker')),
            content TEXT NOT NULL,
            relevance_score REAL NOT NULL DEFAULT 1.0,
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_prospect_memories_prospect
        ON prospect_memories(prospect_context_id, memory_type)
    """)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store(
    prospect_context_id: str,
    memory_type: str,
    content: str,
    relevance_score: float = 1.0,
    source: Optional[str] = None,
) -> str:
    """
    Store a new memory for a prospect.

    Returns the new memory ID.
    """
    if memory_type not in VALID_MEMORY_TYPES:
        raise ValueError(f"Invalid memory_type '{memory_type}'. Must be one of: {VALID_MEMORY_TYPES}")

    memory_id = str(uuid.uuid4())
    now = _now()

    with _get_db() as conn:
        conn.execute("""
            INSERT INTO prospect_memories (id, prospect_context_id, memory_type, content, relevance_score, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (memory_id, prospect_context_id, memory_type, content, relevance_score, source, now, now))

    return memory_id


def get(
    prospect_context_id: str,
    memory_type: Optional[str] = None,
    max_age_days: Optional[int] = None,
    top_k: Optional[int] = None,
    min_relevance: float = 0.1,
) -> list[dict]:
    """
    Retrieve memories for a prospect.

    Args:
        prospect_context_id: The prospect's UUID
        memory_type: Optional filter by type
        max_age_days: Optional filter by age (days since created_at)
        top_k: Optional limit on number of results
        min_relevance: Minimum relevance score (default 0.1)

    Returns:
        List of memory dicts with id, memory_type, content, relevance_score, age_days, created_at
    """
    query = """
        SELECT id, memory_type, content, relevance_score, source, created_at,
               CAST((julianday('now') - julianday(created_at)) AS INTEGER) as age_days
        FROM prospect_memories
        WHERE prospect_context_id = ?
          AND relevance_score >= ?
    """
    params: list = [prospect_context_id, min_relevance]

    if memory_type:
        query += " AND memory_type = ?"
        params.append(memory_type)

    if max_age_days:
        query += " AND (julianday('now') - julianday(created_at)) <= ?"
        params.append(max_age_days)

    query += " ORDER BY relevance_score DESC, created_at DESC"

    if top_k:
        query += f" LIMIT {int(top_k)}"

    with _get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(row) for row in rows]


def update_relevance(memory_id: str, relevance_score: float) -> bool:
    """
    Update the relevance score of a specific memory.

    Returns True if the memory was found and updated.
    """
    with _get_db() as conn:
        cursor = conn.execute("""
            UPDATE prospect_memories
            SET relevance_score = ?, updated_at = ?
            WHERE id = ?
        """, (relevance_score, _now(), memory_id))
        return cursor.rowcount > 0


def summarize(prospect_context_id: str) -> dict:
    """
    Return a structured summary of all memories for a prospect, grouped by type.

    Returns:
        {
            "objections": [...],
            "preferences": [...],
            "signals": [...],
            "relationship": [...],
            "next_steps": [...],
            "blockers": [...],
            "total_memories": int,
        }
    """
    all_memories = get(prospect_context_id, min_relevance=0.1)

    summary: dict = {
        "objections": [],
        "preferences": [],
        "signals": [],
        "relationship": [],
        "next_steps": [],
        "blockers": [],
        "total_memories": len(all_memories),
    }

    type_map = {
        "objection": "objections",
        "preference": "preferences",
        "signal": "signals",
        "relationship": "relationship",
        "next_step": "next_steps",
        "blocker": "blockers",
    }

    for mem in all_memories:
        key = type_map.get(mem["memory_type"])
        if key:
            summary[key].append({
                "content": mem["content"],
                "relevance": mem["relevance_score"],
                "age_days": mem["age_days"],
                "source": mem.get("source"),
            })

    return summary


def decay_old_memories() -> dict:
    """
    Reduce relevance scores for memories that have exceeded their decay threshold.

    Run this weekly to keep the memory directory fresh.

    Returns:
        { "decayed": int, "removed": int }
    """
    decayed = 0
    removed = 0

    with _get_db() as conn:
        for memory_type, threshold_days in DECAY_THRESHOLDS.items():
            # Memories older than 2x threshold get removed entirely
            cursor = conn.execute("""
                DELETE FROM prospect_memories
                WHERE memory_type = ?
                  AND (julianday('now') - julianday(created_at)) > ?
            """, (memory_type, threshold_days * 2))
            removed += cursor.rowcount

            # Memories older than threshold get relevance halved
            cursor = conn.execute("""
                UPDATE prospect_memories
                SET relevance_score = relevance_score * 0.5, updated_at = ?
                WHERE memory_type = ?
                  AND (julianday('now') - julianday(created_at)) > ?
                  AND relevance_score > 0.1
            """, (_now(), memory_type, threshold_days))
            decayed += cursor.rowcount

    return {"decayed": decayed, "removed": removed}


def stats() -> dict:
    """Return memory statistics for all prospects."""
    with _get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM prospect_memories").fetchone()[0]
        by_type = conn.execute("""
            SELECT memory_type, COUNT(*) as count, AVG(relevance_score) as avg_relevance
            FROM prospect_memories
            GROUP BY memory_type
        """).fetchall()
        prospect_count = conn.execute(
            "SELECT COUNT(DISTINCT prospect_context_id) FROM prospect_memories"
        ).fetchone()[0]

    return {
        "total_memories": total,
        "prospect_count": prospect_count,
        "by_type": [dict(row) for row in by_type],
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python3 prospect_memories.py <command> [args]")
        print("Commands: stats, decay, get <prospect_id>, summarize <prospect_id>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "stats":
        print(json.dumps(stats(), indent=2))

    elif cmd == "decay":
        result = decay_old_memories()
        print(json.dumps(result, indent=2))

    elif cmd == "get" and len(sys.argv) >= 3:
        memories = get(sys.argv[2])
        print(json.dumps(memories, indent=2))

    elif cmd == "summarize" and len(sys.argv) >= 3:
        summary = summarize(sys.argv[2])
        print(json.dumps(summary, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
