"""
Samscloud Prospect Memory Directory
====================================
Structured per-prospect memory storage for the OpenClaw agent.

Stores and retrieves typed facts about individual prospects:
- objections, preferences, signals, relationship context, next steps, blockers

Auto-ingests from:
  1. Brevo CRM — email engagement events (opens, clicks, replies)
  2. call_events — outbound call transcripts, outcomes, analysis
  3. inbound_call_logs — inbound call transcripts, summaries, sentiment
  4. prospect_contexts — notes field, known_objections array, crm_history

Storage: SQLite at /root/.openclaw/memory/prospect_memories.db

Usage:
    import sys
    sys.path.insert(0, "/root/.openclaw/skills/samscloud-prospect-memories/scripts")
    import prospect_memories as pm

    # Manual store
    pm.store(prospect_context_id="uuid", memory_type="objection", content="...")

    # Auto-ingest from all data sources
    result = pm.ingest(prospect_context_id="uuid")

    # Retrieve
    memories = pm.get(prospect_context_id="uuid")
    summary = pm.summarize(prospect_context_id="uuid")
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Config — all values read from environment (set in openclaw.json env section)
# ---------------------------------------------------------------------------

MEMORY_DIR = Path(os.environ.get("OPENCLAW_STATE_DIR", Path.home() / ".openclaw")) / "memory"
DB_PATH = MEMORY_DIR / "prospect_memories.db"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://blcwlsqjmzslvzxjtjbx.supabase.co")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SAMSCLOUD_AUTH_TOKEN = os.environ.get("SAMSCLOUD_AUTH_TOKEN", "")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")

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
            source_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_prospect_memories_prospect
        ON prospect_memories(prospect_context_id, memory_type)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingest_log (
            id TEXT PRIMARY KEY,
            prospect_context_id TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT,
            status TEXT NOT NULL,
            memories_created INTEGER DEFAULT 0,
            processed_at TEXT NOT NULL,
            notes TEXT
        )
    """)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — no external deps)
# ---------------------------------------------------------------------------

def _http_get(url: str, headers: dict) -> dict:
    """Make a GET request and return parsed JSON."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code} GET {url}: {body[:200]}")


def _supabase_get(path: str, params: str = "") -> list:
    """Query Supabase REST API and return rows."""
    url = f"{SUPABASE_URL}/rest/v1/{path}?{params}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SAMSCLOUD_AUTH_TOKEN}",
        "Accept": "application/json",
    }
    result = _http_get(url, headers)
    if isinstance(result, list):
        return result
    return []


def _supabase_edge(function_name: str, payload: dict) -> dict:
    """Call a Supabase edge function and return the response."""
    url = f"{SUPABASE_URL}/functions/v1/{function_name}"
    data = json.dumps(payload).encode()
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SAMSCLOUD_AUTH_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code} POST {function_name}: {body[:200]}")


def _brevo_get(path: str) -> dict:
    """Call the Brevo API directly."""
    url = f"https://api.brevo.com/v3/{path}"
    headers = {
        "api-key": BREVO_API_KEY,
        "Accept": "application/json",
    }
    return _http_get(url, headers)


# ---------------------------------------------------------------------------
# Core memory operations
# ---------------------------------------------------------------------------

def store(
    prospect_context_id: str,
    memory_type: str,
    content: str,
    relevance_score: float = 1.0,
    source: Optional[str] = None,
    source_id: Optional[str] = None,
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
            INSERT INTO prospect_memories
                (id, prospect_context_id, memory_type, content, relevance_score, source, source_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (memory_id, prospect_context_id, memory_type, content, relevance_score, source, source_id, now, now))

    return memory_id


def _already_ingested(source: str, source_id: str) -> bool:
    """Check if a source record has already been ingested to avoid duplicates."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT id FROM ingest_log WHERE source = ? AND source_id = ? AND status = 'ok'",
            (source, source_id)
        ).fetchone()
        return row is not None


def _log_ingest(prospect_context_id: str, source: str, source_id: Optional[str],
                status: str, memories_created: int, notes: str = "") -> None:
    with _get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO ingest_log
                (id, prospect_context_id, source, source_id, status, memories_created, processed_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), prospect_context_id, source, source_id, status, memories_created, _now(), notes))


# ---------------------------------------------------------------------------
# Ingestion: Brevo email engagement events
# ---------------------------------------------------------------------------

def _ingest_brevo_engagement(prospect_context_id: str) -> int:
    """
    Pull email engagement events from Brevo for this prospect and store as memories.
    Maps: opened → signal, clicked → signal, replied → signal, unsubscribed → blocker
    """
    created = 0
    try:
        resp = _supabase_edge("brevo-crm-sync", {
            "action": "get_engagement",
            "prospect_id": prospect_context_id,
        })
        events = resp.get("events", [])

        # Aggregate events to avoid storing one memory per email
        opened_count = 0
        clicked_count = 0
        replied = False
        unsubscribed = False
        last_open_date = None

        for event in events:
            evt = event.get("event", "").lower()
            date = event.get("date", "")
            if evt in ("opened", "uniqueopened"):
                opened_count += 1
                if not last_open_date or date > last_open_date:
                    last_open_date = date
            elif evt == "click":
                clicked_count += 1
            elif evt == "reply":
                replied = True
            elif evt in ("unsubscribe", "hardBounce", "softBounce"):
                unsubscribed = True

        source_key = f"brevo_engagement_{prospect_context_id}"

        if unsubscribed and not _already_ingested("brevo_engagement_unsub", prospect_context_id):
            store(prospect_context_id, "blocker",
                  "Prospect has unsubscribed or bounced from email communications.",
                  relevance_score=1.0, source="brevo_email", source_id=prospect_context_id)
            _log_ingest(prospect_context_id, "brevo_engagement_unsub", prospect_context_id, "ok", 1)
            created += 1

        if replied and not _already_ingested("brevo_engagement_reply", prospect_context_id):
            store(prospect_context_id, "signal",
                  "Prospect has replied to at least one email.",
                  relevance_score=0.9, source="brevo_email", source_id=prospect_context_id)
            _log_ingest(prospect_context_id, "brevo_engagement_reply", prospect_context_id, "ok", 1)
            created += 1

        if opened_count >= 3 and not replied and not _already_ingested("brevo_engagement_opens", prospect_context_id):
            store(prospect_context_id, "signal",
                  f"Prospect has opened {opened_count} emails but has not replied. Last open: {last_open_date or 'unknown'}.",
                  relevance_score=0.75, source="brevo_email", source_id=prospect_context_id)
            _log_ingest(prospect_context_id, "brevo_engagement_opens", prospect_context_id, "ok", 1)
            created += 1
        elif opened_count == 1 and not _already_ingested("brevo_engagement_single_open", prospect_context_id):
            store(prospect_context_id, "signal",
                  "Prospect has opened 1 email. Early engagement signal.",
                  relevance_score=0.5, source="brevo_email", source_id=prospect_context_id)
            _log_ingest(prospect_context_id, "brevo_engagement_single_open", prospect_context_id, "ok", 1)
            created += 1

        if clicked_count > 0 and not _already_ingested("brevo_engagement_clicks", prospect_context_id):
            store(prospect_context_id, "signal",
                  f"Prospect has clicked links in {clicked_count} email(s). High intent signal.",
                  relevance_score=0.85, source="brevo_email", source_id=prospect_context_id)
            _log_ingest(prospect_context_id, "brevo_engagement_clicks", prospect_context_id, "ok", 1)
            created += 1

    except Exception as e:
        _log_ingest(prospect_context_id, "brevo_engagement", None, "error", 0, str(e))

    return created


# ---------------------------------------------------------------------------
# Ingestion: Brevo CRM contact notes (openclaw_activity timeline events)
# ---------------------------------------------------------------------------

def _ingest_brevo_contact_notes(prospect_context_id: str, contact_email: str) -> int:
    """
    Pull openclaw_activity timeline events from Brevo for this contact.
    These are notes logged by the brevo-crm-sync log_note action.
    """
    created = 0
    if not BREVO_API_KEY or not contact_email:
        return 0
    try:
        # Brevo contacts events endpoint
        data = _brevo_get(f"contacts/{urllib.request.quote(contact_email, safe='')}/campaignStats")
        # Also try the transactional events
        events_data = _brevo_get(
            f"smtp/statistics/events?email={urllib.request.quote(contact_email, safe='')}&limit=100&sort=desc"
        )
        events = events_data.get("events", [])

        for event in events:
            if event.get("event") == "openclaw_activity":
                event_id = event.get("messageId") or event.get("id") or str(event.get("date", ""))
                source_id = f"brevo_note_{event_id}"
                if _already_ingested("brevo_note", source_id):
                    continue
                note_data = event.get("event_data", {})
                outcome = note_data.get("outcome", "")
                skill = note_data.get("skill", "")
                channel = note_data.get("channel", "")
                content = f"CRM activity logged: [{skill}] via {channel}. Outcome: {outcome}."
                store(prospect_context_id, "signal", content,
                      relevance_score=0.6, source="brevo_crm_note", source_id=source_id)
                _log_ingest(prospect_context_id, "brevo_note", source_id, "ok", 1)
                created += 1
    except Exception as e:
        _log_ingest(prospect_context_id, "brevo_contact_notes", None, "error", 0, str(e))
    return created


# ---------------------------------------------------------------------------
# Ingestion: call_events (outbound calls via Retell)
# ---------------------------------------------------------------------------

def _ingest_call_events(prospect_context_id: str, contact_email: str) -> int:
    """
    Pull outbound call events from call_events table.
    Maps call outcomes and transcripts to memories.
    """
    created = 0
    try:
        # Find campaign_contacts matching this prospect's email
        contacts = _supabase_get(
            "campaign_contacts",
            f"email=eq.{urllib.request.quote(contact_email, safe='')}&select=id,email"
        )
        if not contacts:
            return 0

        contact_ids = [c["id"] for c in contacts]

        for contact_id in contact_ids:
            calls = _supabase_get(
                "call_events",
                f"contact_id=eq.{contact_id}&select=id,outcome,transcript,analysis,duration,started_at&order=started_at.desc&limit=20"
            )
            for call in calls:
                call_id = call.get("id")
                source_id = f"call_event_{call_id}"
                if _already_ingested("call_event", source_id):
                    continue

                outcome = call.get("outcome", "")
                transcript = call.get("transcript", "") or ""
                analysis = call.get("analysis") or {}
                duration = call.get("duration", 0) or 0
                started_at = call.get("started_at", "")

                # Map outcome to memory type
                if outcome == "dnc":
                    store(prospect_context_id, "blocker",
                          f"Prospect requested Do Not Call during call on {started_at[:10]}.",
                          relevance_score=1.0, source="call_event", source_id=source_id)
                    _log_ingest(prospect_context_id, "call_event", source_id, "ok", 1)
                    created += 1

                elif outcome == "booked":
                    store(prospect_context_id, "next_step",
                          f"Meeting booked during call on {started_at[:10]}. Duration: {duration}s.",
                          relevance_score=1.0, source="call_event", source_id=source_id)
                    _log_ingest(prospect_context_id, "call_event", source_id, "ok", 1)
                    created += 1

                elif outcome == "answered" and duration > 60:
                    # Extract objections from analysis if available
                    objections = []
                    if isinstance(analysis, dict):
                        objections = analysis.get("objections", []) or []
                        sentiment = analysis.get("sentiment", "")
                        next_steps = analysis.get("next_steps", []) or []

                        for obj in objections:
                            if isinstance(obj, str) and obj.strip():
                                store(prospect_context_id, "objection", obj.strip(),
                                      relevance_score=0.9, source="call_transcript", source_id=source_id)
                                created += 1

                        for step in next_steps:
                            if isinstance(step, str) and step.strip():
                                store(prospect_context_id, "next_step", step.strip(),
                                      relevance_score=0.85, source="call_transcript", source_id=source_id)
                                created += 1

                        if sentiment in ("negative", "very_negative"):
                            store(prospect_context_id, "signal",
                                  f"Negative sentiment detected on call ({started_at[:10]}). Duration: {duration}s.",
                                  relevance_score=0.7, source="call_event", source_id=source_id)
                            created += 1

                    # If no structured analysis, store a general signal
                    if not objections and duration > 120:
                        store(prospect_context_id, "signal",
                              f"Answered call on {started_at[:10]}, spoke for {duration}s. No objections extracted.",
                              relevance_score=0.6, source="call_event", source_id=source_id)
                        created += 1

                    _log_ingest(prospect_context_id, "call_event", source_id, "ok", created)

                elif outcome in ("voicemail", "no_answer"):
                    # Only store a signal if there have been multiple no-answers
                    pass  # Handled in aggregate below

        # Check for repeated no-answers (blocker signal)
        total_no_answers = sum(
            1 for c_id in contact_ids
            for call in _supabase_get(
                "call_events",
                f"contact_id=eq.{c_id}&outcome=eq.no_answer&select=id"
            )
        )
        if total_no_answers >= 3:
            agg_source_id = f"call_no_answer_agg_{prospect_context_id}"
            if not _already_ingested("call_no_answer_agg", agg_source_id):
                store(prospect_context_id, "signal",
                      f"Prospect has not answered {total_no_answers} outbound call attempts.",
                      relevance_score=0.65, source="call_event", source_id=agg_source_id)
                _log_ingest(prospect_context_id, "call_no_answer_agg", agg_source_id, "ok", 1)
                created += 1

    except Exception as e:
        _log_ingest(prospect_context_id, "call_events", None, "error", 0, str(e))

    return created


# ---------------------------------------------------------------------------
# Ingestion: inbound_call_logs (inbound calls)
# ---------------------------------------------------------------------------

def _ingest_inbound_calls(prospect_context_id: str, contact_phone: str) -> int:
    """
    Pull inbound call logs matching this prospect's phone number.
    Extracts summaries, sentiment, topics, and follow-up notes as memories.
    """
    created = 0
    if not contact_phone:
        return 0
    try:
        calls = _supabase_get(
            "inbound_call_logs",
            f"caller_phone=eq.{urllib.request.quote(contact_phone, safe='')}&select=id,transcript,summary,outcome,sentiment,topics,follow_up_notes,started_at&order=started_at.desc&limit=10"
        )
        for call in calls:
            call_id = call.get("id")
            source_id = f"inbound_call_{call_id}"
            if _already_ingested("inbound_call", source_id):
                continue

            summary = call.get("summary", "") or ""
            sentiment = call.get("sentiment", "") or ""
            topics = call.get("topics") or []
            follow_up = call.get("follow_up_notes", "") or ""
            started_at = call.get("started_at", "")[:10]

            if summary:
                store(prospect_context_id, "signal",
                      f"Inbound call on {started_at}: {summary}",
                      relevance_score=0.85, source="inbound_call", source_id=source_id)
                created += 1

            if follow_up:
                store(prospect_context_id, "next_step",
                      f"Follow-up from inbound call ({started_at}): {follow_up}",
                      relevance_score=0.9, source="inbound_call", source_id=source_id)
                created += 1

            if sentiment in ("negative", "frustrated"):
                store(prospect_context_id, "signal",
                      f"Negative/frustrated sentiment on inbound call ({started_at}).",
                      relevance_score=0.75, source="inbound_call", source_id=source_id)
                created += 1

            if topics:
                topic_str = ", ".join(topics) if isinstance(topics, list) else str(topics)
                store(prospect_context_id, "signal",
                      f"Topics discussed on inbound call ({started_at}): {topic_str}.",
                      relevance_score=0.7, source="inbound_call", source_id=source_id)
                created += 1

            _log_ingest(prospect_context_id, "inbound_call", source_id, "ok", created)

    except Exception as e:
        _log_ingest(prospect_context_id, "inbound_calls", None, "error", 0, str(e))

    return created


# ---------------------------------------------------------------------------
# Ingestion: prospect_contexts notes + known_objections + crm_history
# ---------------------------------------------------------------------------

def _ingest_prospect_context(prospect_context_id: str) -> int:
    """
    Pull the prospect_contexts record and extract:
    - notes field → relationship memory
    - known_objections array → objection memories
    - crm_history JSONB → signal memories
    """
    created = 0
    try:
        rows = _supabase_get(
            "prospect_contexts",
            f"id=eq.{prospect_context_id}&select=notes,known_objections,crm_history,recent_activity,contact_phone"
        )
        if not rows:
            return 0
        prospect = rows[0]

        notes = prospect.get("notes", "") or ""
        known_objections = prospect.get("known_objections") or []
        crm_history = prospect.get("crm_history") or []
        recent_activity = prospect.get("recent_activity") or []

        # Notes field
        if notes and not _already_ingested("prospect_notes", prospect_context_id):
            store(prospect_context_id, "relationship",
                  f"CRM notes: {notes}",
                  relevance_score=0.8, source="prospect_context", source_id=prospect_context_id)
            _log_ingest(prospect_context_id, "prospect_notes", prospect_context_id, "ok", 1)
            created += 1

        # Known objections array
        for i, obj in enumerate(known_objections):
            if not isinstance(obj, str) or not obj.strip():
                continue
            source_id = f"prospect_obj_{prospect_context_id}_{i}"
            if not _already_ingested("prospect_objection", source_id):
                store(prospect_context_id, "objection", obj.strip(),
                      relevance_score=0.85, source="prospect_context", source_id=source_id)
                _log_ingest(prospect_context_id, "prospect_objection", source_id, "ok", 1)
                created += 1

        # CRM history entries
        if isinstance(crm_history, list):
            for entry in crm_history[:10]:  # cap at 10 most recent
                if not isinstance(entry, dict):
                    continue
                entry_id = entry.get("id") or entry.get("date") or str(hash(json.dumps(entry, sort_keys=True)))
                source_id = f"crm_history_{prospect_context_id}_{entry_id}"
                if _already_ingested("crm_history", source_id):
                    continue
                content = entry.get("note") or entry.get("summary") or entry.get("description") or ""
                if content:
                    store(prospect_context_id, "relationship", content,
                          relevance_score=0.7, source="crm_history", source_id=source_id)
                    _log_ingest(prospect_context_id, "crm_history", source_id, "ok", 1)
                    created += 1

    except Exception as e:
        _log_ingest(prospect_context_id, "prospect_context", None, "error", 0, str(e))

    return created


# ---------------------------------------------------------------------------
# Master ingest function
# ---------------------------------------------------------------------------

def ingest(prospect_context_id: str) -> dict:
    """
    Auto-ingest memories from all data sources for a prospect.

    Sources:
    1. Brevo email engagement events
    2. Brevo CRM contact notes (openclaw_activity timeline)
    3. call_events (outbound calls)
    4. inbound_call_logs (inbound calls)
    5. prospect_contexts (notes, known_objections, crm_history)

    Returns:
        {
            "prospect_context_id": str,
            "total_created": int,
            "by_source": { source: count, ... },
            "errors": [ ... ]
        }
    """
    by_source: dict[str, int] = {}
    errors: list[str] = []

    # Fetch prospect details needed for cross-source lookups
    contact_email = ""
    contact_phone = ""
    try:
        rows = _supabase_get(
            "prospect_contexts",
            f"id=eq.{prospect_context_id}&select=contact_email,contact_phone"
        )
        if rows:
            contact_email = rows[0].get("contact_email", "") or ""
            contact_phone = rows[0].get("contact_phone", "") or ""
    except Exception as e:
        errors.append(f"prospect_lookup: {e}")

    # 1. Brevo engagement events
    try:
        n = _ingest_brevo_engagement(prospect_context_id)
        by_source["brevo_engagement"] = n
    except Exception as e:
        errors.append(f"brevo_engagement: {e}")
        by_source["brevo_engagement"] = 0

    # 2. Brevo CRM contact notes
    if contact_email:
        try:
            n = _ingest_brevo_contact_notes(prospect_context_id, contact_email)
            by_source["brevo_notes"] = n
        except Exception as e:
            errors.append(f"brevo_notes: {e}")
            by_source["brevo_notes"] = 0

    # 3. Outbound call events
    if contact_email:
        try:
            n = _ingest_call_events(prospect_context_id, contact_email)
            by_source["call_events"] = n
        except Exception as e:
            errors.append(f"call_events: {e}")
            by_source["call_events"] = 0

    # 4. Inbound call logs
    if contact_phone:
        try:
            n = _ingest_inbound_calls(prospect_context_id, contact_phone)
            by_source["inbound_calls"] = n
        except Exception as e:
            errors.append(f"inbound_calls: {e}")
            by_source["inbound_calls"] = 0

    # 5. Prospect context fields
    try:
        n = _ingest_prospect_context(prospect_context_id)
        by_source["prospect_context"] = n
    except Exception as e:
        errors.append(f"prospect_context: {e}")
        by_source["prospect_context"] = 0

    total = sum(by_source.values())
    return {
        "prospect_context_id": prospect_context_id,
        "total_created": total,
        "by_source": by_source,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

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
    """Update the relevance score of a specific memory. Returns True if found."""
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
    """
    decayed = 0
    removed = 0

    with _get_db() as conn:
        for memory_type, threshold_days in DECAY_THRESHOLDS.items():
            cursor = conn.execute("""
                DELETE FROM prospect_memories
                WHERE memory_type = ?
                  AND (julianday('now') - julianday(created_at)) > ?
            """, (memory_type, threshold_days * 2))
            removed += cursor.rowcount

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

    if len(sys.argv) < 2:
        print("Usage: python3 prospect_memories.py <command> [args]")
        print("Commands: stats, decay, get <prospect_id>, summarize <prospect_id>, ingest <prospect_id>")
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

    elif cmd == "ingest" and len(sys.argv) >= 3:
        result = ingest(sys.argv[2])
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
