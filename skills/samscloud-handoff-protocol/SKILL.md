---
name: samscloud-handoff-protocol
description: Defines the exact trigger points where the agent must stop replying and loop in a human sales rep. Use this skill when a prospect asks for pricing, a demo, or highly technical questions.
---

# Samscloud Handoff Protocol

This skill provides the strict rules for when the OpenClaw agent must stop autonomous communication and hand off the conversation to a human sales representative. The agent's goal is to start conversations and qualify leads, not to close enterprise deals.

## Handoff Triggers

The agent MUST immediately stop replying and initiate the handoff protocol if the prospect:

1. **Asks for Pricing:** "How much does this cost?", "What's the pricing model?", "Can you send a quote?"
2. **Requests a Demo:** "I'd like to see a demo," "Can we schedule a call?", "Let's set up a meeting."
3. **Asks Highly Technical Questions:** "How does your API integrate with our specific VMS?", "What's your latency on the video feed?", "Can you explain your patented architecture in detail?"
4. **Expresses Strong Interest:** "This sounds exactly like what we need," "We've been looking for a solution like this."
5. **Mentions a Competitor Contract Expiring Soon:** "Our contract with [Competitor] is up next month."
6. **Is a High-Value Target (e.g., Superintendent of a Top 100 District):** Any reply from a top-tier prospect should be flagged for human review.

## Handoff Procedure

When a handoff trigger is detected, the agent must:

1. **Stop Replying:** Do not send any further messages to the prospect.
2. **Tag the CRM Record:** Update the prospect's record in Brevo with the tag "Human Intervention Required" or move them to the "Demo Requested" stage.
3. **Notify the Human Operator:** Send an alert via Telegram or Slack to the sales team with the prospect's name, company, and the specific message that triggered the handoff.
4. **Log the Handoff:** Record the handoff event in the `skill_hook_logs` for auditing purposes.

## Example Handoff Alert

"🚨 **Handoff Required:** [Prospect Name] at [District/Company] just asked for pricing. I have stopped replying and tagged their CRM record. Please review the conversation and follow up."
