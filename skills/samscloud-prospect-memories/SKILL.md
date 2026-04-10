---
name: samscloud-prospect-memories
description: Structured per-prospect memory directory for OpenClaw. Stores and retrieves typed facts about individual prospects (objections, preferences, signals, relationship context, next steps, blockers). Auto-ingests from Brevo CRM email engagement, outbound call transcripts (call_events), inbound call logs, and prospect_contexts notes. Use this skill when you need to recall what you know about a prospect before crafting outreach, or when you want to record a new insight about a prospect.
---

# Samscloud Prospect Memory Directory

This skill provides a structured memory layer for individual prospects. While `samscloud-memory` tracks content performance across all prospects, this skill tracks what is known about each specific prospect — their objections, preferences, engagement history, and relationship stage.

## When to Use This Skill

- Before drafting outreach: retrieve existing memories to personalize the message
- After a prospect replies: store their objection or signal as a new memory
- After a meeting or call: store key facts and next steps
- During sequence planning: check memory age and relevance to decide the next action

## Memory Types

| Type | Description | Example |
|---|---|---|
| `objection` | A concern or pushback the prospect has raised | "Already have a solution in place" |
| `preference` | A communication or content preference | "Prefers short emails, no attachments" |
| `signal` | A behavioral or engagement signal | "Opened 3 emails but never replied" |
| `relationship` | A relationship context fact | "Connected to Sam via LinkedIn in March" |
| `next_step` | A committed or planned next action | "Agreed to review the one-pager by Friday" |
| `blocker` | A known obstacle to progression | "Budget cycle doesn't open until Q3" |

## Automatic Data Ingestion

The `ingest()` function pulls from **5 data sources** automatically. Call it before any outreach to ensure memories are up to date:

```python
import sys
sys.path.insert(0, "/root/.openclaw/skills/samscloud-prospect-memories/scripts")
import prospect_memories as pm

result = pm.ingest(prospect_context_id="<uuid>")
print(f"Ingested {result['total_created']} new memories")
print(result['by_source'])  # { brevo_engagement: 2, call_events: 3, ... }
```

### Data Sources

| Source | Table / API | Memory Types Created |
|---|---|---|
| **Brevo email engagement** | `brevo-crm-sync` edge fn → Brevo API | `signal` (opens/clicks/replies), `blocker` (unsubscribed) |
| **Brevo CRM notes** | `openclaw_activity` timeline events | `signal` (past outreach outcomes) |
| **Outbound calls** | `call_events` Supabase table | `blocker` (DNC), `next_step` (booked), `objection` (from transcript analysis) |
| **Inbound calls** | `inbound_call_logs` Supabase table | `signal` (summary), `next_step` (follow-up notes), `signal` (sentiment/topics) |
| **Prospect context** | `prospect_contexts` table | `relationship` (notes), `objection` (known_objections), `relationship` (crm_history) |

Ingestion is **idempotent** — running `ingest()` multiple times will not create duplicate memories. Each source record is tracked in an `ingest_log` table.

### Checking for Blockers Before Outreach

Always check for blockers before initiating any outreach:

```python
blockers = pm.get(prospect_context_id="<uuid>", memory_type="blocker")
if blockers:
    print("STOP: Prospect has active blockers:", [b["content"] for b in blockers])
```

---

## Python Interface

The prospect memories module lives at:
`/root/.openclaw/skills/samscloud-prospect-memories/scripts/prospect_memories.py`

### Store a memory
```python
import sys
sys.path.insert(0, "/root/.openclaw/skills/samscloud-prospect-memories/scripts")
import prospect_memories as pm

pm.store(
    prospect_context_id="uuid-of-prospect",
    memory_type="objection",
    content="They said they already have Avigilon and are happy with it.",
    relevance_score=0.9,
    source="email_reply",
)
```

### Retrieve memories for a prospect
```python
memories = pm.get(
    prospect_context_id="uuid-of-prospect",
    memory_type="objection",   # optional filter
    max_age_days=90,           # optional age filter
    top_k=5,                   # optional limit
)
# Returns: list of { id, memory_type, content, relevance_score, age_days, created_at }
```

### Update a memory's relevance
```python
pm.update_relevance(memory_id="uuid", relevance_score=0.3)
```

### Summarize all memories for a prospect
```python
summary = pm.summarize(prospect_context_id="uuid-of-prospect")
# Returns: { objections: [...], preferences: [...], signals: [...], blockers: [...], next_steps: [...] }
```

## Integration with the Orchestration Loop

In the 13-step orchestration loop, prospect memories are used at these steps:

**Step 2 (load prospect context):** Call `pm.get(prospect_context_id)` to load all memories. Inject them into the drafting context.

**Step 4 (decide reuse/adapt/generate):** If `objection` memories exist, ensure the draft addresses them. If `preference` memories exist, apply them to the format.

**Step 11 (after reply received):** Call `pm.store()` with the reply content classified as `objection`, `signal`, or `next_step`.

**Step 13 (write outcomes):** Update `relevance_score` for memories that were addressed or resolved.

## CLI Commands

```bash
# View stats across all prospects
python3 /root/.openclaw/skills/samscloud-prospect-memories/scripts/prospect_memories.py stats

# Ingest all data sources for a prospect
python3 /root/.openclaw/skills/samscloud-prospect-memories/scripts/prospect_memories.py ingest <prospect_id>

# Get all memories for a prospect
python3 /root/.openclaw/skills/samscloud-prospect-memories/scripts/prospect_memories.py get <prospect_id>

# Get a structured summary
python3 /root/.openclaw/skills/samscloud-prospect-memories/scripts/prospect_memories.py summarize <prospect_id>

# Run decay cleanup
python3 /root/.openclaw/skills/samscloud-prospect-memories/scripts/prospect_memories.py decay
```

## Environment Variables Required

| Variable | Purpose |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase anon key |
| `SAMSCLOUD_AUTH_TOKEN` | Authenticated user JWT for RLS-protected queries |
| `BREVO_API_KEY` | Brevo API key for direct engagement queries |
| `OPENCLAW_STATE_DIR` | Base directory for SQLite DB (default: `~/.openclaw`) |

## Storage

Memories are stored in SQLite at:
`/root/.openclaw/memory/prospect_memories.db`

This is a separate database from `samscloud_memory.db` to keep prospect-level facts isolated from content performance data.

## Memory Aging

Memories decay in relevance over time:
- `objection` — relevant for 180 days
- `preference` — relevant for 365 days
- `signal` — relevant for 60 days
- `relationship` — relevant for 365 days
- `next_step` — relevant for 14 days (stale if not acted on)
- `blocker` — relevant for 90 days

Run `pm.decay_old_memories()` weekly to reduce relevance scores for aged memories.
