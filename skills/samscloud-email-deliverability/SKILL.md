---
name: samscloud-email-deliverability
description: Enforces email deliverability best practices, daily send limits, and domain reputation protection. Use this skill BEFORE sending any outbound email via Brevo or other email tools.
---

# Samscloud Email Deliverability & Domain Protection

This skill defines the strict rules for sending outbound emails to protect the `samscloud.io` domain reputation and ensure high inbox placement rates.

## Core Deliverability Rules

1. **No HTML in First Touch:** The first email sent to any prospect MUST be 100% plain text. No images, no complex formatting, no tracking pixels if possible.
2. **No Links in First Touch:** Do not include any URLs or links in the first email, not even to the Samscloud website. Links in cold emails trigger spam filters.
3. **Daily Send Limits:** Never send more than 40 cold emails per day per mailbox. If the daily limit is reached, queue the remaining emails for the next day.
4. **Bounce Handling:** If an email bounces, immediately mark the contact as "Bounced" in Brevo and DO NOT attempt to email them again.
5. **Spam Word Avoidance:** Avoid words that trigger spam filters (e.g., "Free", "Guarantee", "Act Now", "Limited Time", "Risk-Free"). Use the NEPQ framework to ask questions instead of making claims.

## Pre-Send Checklist

Before executing any email sending tool, verify:
- Is this the first touch? If yes, strip all links and HTML.
- Has the daily send limit been reached?
- Is the prospect's email address verified or highly confident?
- Does the subject line avoid spam trigger words?

## Handling Blocks

If the email sending tool returns an error indicating an IP block or spam rejection:
1. STOP sending immediately.
2. Log the error in the `skill_hook_logs`.
3. Notify the human operator via the `samscloud-handoff-protocol`.
