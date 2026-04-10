---
name: samscloud-prospect-memories
description: Structured per-prospect memory directory. Use this skill when you need to store, retrieve, or update facts about a specific prospect — including objections, preferences, engagement signals, and relationship context. Extends the samscloud-memory content system with prospect-level intelligence.
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
