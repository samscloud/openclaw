---
name: samscloud-skill-audit
description: "Pre/Post tool hook that logs all Samscloud skill executions and enforces permission policy before tool use"
homepage: https://github.com/samscloud/openclaw
metadata:
  {
    "openclaw":
      {
        "emoji": "🔐",
        "events": ["agent", "command"],
        "install": [{ "id": "bundled", "kind": "bundled", "label": "Bundled with OpenClaw" }],
      },
  }
---

# Samscloud Skill Audit Hook

This hook provides pre/post tool execution auditing for all Samscloud skill runs. It logs every agent action and enforces the Samscloud permission policy before any outreach or email tool is invoked.

## What It Does

### Pre-Tool (before execution)
1. **Checks permission policy** — Blocks `send-brevo-email`, `functions`, and `browser` tool calls unless the skill run has `approval_status = approved` in the Supabase backend.
2. **Logs the intent** — Writes a JSON line to `~/.openclaw/logs/skill-audit.log` with the tool name, session key, and timestamp.
3. **Injects context** — Adds the current prospect context ID to the agent's working memory if available.

### Post-Tool (after execution)
1. **Records outcome** — Appends the tool result status (success/failure) to the audit log.
2. **Triggers memory update** — If the tool was `send-brevo-email` and succeeded, flags the skill run for memory ingestion.

## Audit Log Location

`~/.openclaw/logs/skill-audit.log`

Each entry is a JSON line:
```json
{"timestamp":"2026-04-10T12:00:00.000Z","phase":"pre","tool":"send-brevo-email","sessionKey":"agent:main:main","allowed":true}
{"timestamp":"2026-04-10T12:00:01.000Z","phase":"post","tool":"send-brevo-email","sessionKey":"agent:main:main","status":"success"}
```

## Permission Policy

The following tools require explicit approval before execution:

| Tool | Approval Required | Reason |
|---|---|---|
| `send-brevo-email` | Yes | Outbound email to prospects |
| `functions` (skillrun-api) | Yes | Backend skill run creation |
| `browser` (form submit) | Yes | Any form submission action |

Tools that are always allowed without approval:
- `memory`, `fs`, `sessions`, `runtime`, `browser` (read-only)

## Configuration

Enable this hook in your `openclaw.json`:

```json
{
  "hooks": {
    "internal": {
      "enabled": true,
      "entries": {
        "samscloud-skill-audit": {
          "enabled": true
        }
      }
    }
  }
}
```

## Disabling

```bash
openclaw hooks disable samscloud-skill-audit
```
