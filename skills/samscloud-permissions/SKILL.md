---
name: samscloud-permissions
description: Samscloud outreach permission policy. Use this skill to determine whether an action is allowed before executing any outbound communication, email send, or CRM update.
---

# Samscloud Permission Policy

This skill defines the approval and permission rules that govern all outbound actions in the Samscloud agent system. Before executing any tool that sends an email, updates a CRM record, or submits a form, you MUST check this policy.

## Core Rule

**Never send an outbound communication without explicit approval.**

The approval chain is:
1. Draft content is created and stored as a `skill_run` with `approval_status = pending`
2. The human operator reviews and approves the draft in the Samscloud backend
3. Only after `approval_status = approved` may the send tool be invoked

## Permission Tiers

| Action | Tier | Approval Required |
|---|---|---|
| Draft an email or LinkedIn message | Tier 1 — Draft | None — always allowed |
| Research a prospect or read CRM data | Tier 1 — Read | None — always allowed |
| Update a CRM note or tag | Tier 2 — Write | Soft — log but proceed |
| Send an email via Brevo | Tier 3 — Send | Hard — must be `approved` |
| Submit a LinkedIn message | Tier 3 — Send | Hard — must be `approved` |
| Book a calendar meeting | Tier 3 — Send | Hard — must be `approved` |
| Bulk send (>1 recipient) | Tier 4 — Bulk | Hard + explicit confirmation |

## Pre-Send Checklist

Before invoking `send-brevo-email` or any outbound tool, verify ALL of the following:

1. **Approval status** — The `skill_run` record has `approval_status = approved` in Supabase
2. **Recipient accuracy** — The `to_email` matches the intended prospect, not a test address
3. **Content review** — The `draft_content` has been reviewed and has no placeholder text (e.g., `[Name]`, `[District]`)
4. **Sequence position** — This is the correct step in the prospect's outreach sequence (not a duplicate)
5. **Opt-out check** — The prospect has not replied with a stop/unsubscribe signal

If any check fails, STOP and notify the user before proceeding.

## Denied Tool Patterns

The following actions are ALWAYS denied regardless of approval status:

- Sending to more than 10 recipients in a single session without explicit user confirmation
- Using browser automation to submit any payment or financial form
- Deleting CRM contacts or deals
- Accessing any credential or API key outside of the `env` section of `openclaw.json`

## Logging

Every permission decision (allow or deny) must be logged to `~/.openclaw/logs/skill-audit.log` with:
- `timestamp`
- `tool` name
- `decision` (allowed / denied)
- `reason`
- `session_key`

## Escalation

If you are uncertain whether an action is permitted, DO NOT proceed. Instead:
1. Write a note to `memory/YYYY-MM-DD.md` describing the pending action
2. Send a message to the user asking for explicit confirmation
3. Wait for a reply before continuing
