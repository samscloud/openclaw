/**
 * Samscloud Skill Audit Hook Handler
 *
 * Pre/Post tool hook that:
 * 1. Logs all Samscloud skill executions to an audit log
 * 2. Enforces permission policy before outreach/email tools are invoked
 * 3. Flags successful email sends for memory ingestion
 *
 * Audit log: ~/.openclaw/logs/skill-audit.log
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { HookHandler } from "../../hooks.js";

/**
 * Tools that require explicit approval before execution.
 * These are outbound-action tools that can send emails or submit forms.
 */
const APPROVAL_REQUIRED_TOOLS = new Set([
  "send-brevo-email",
  "functions",
]);

/**
 * Tool name patterns that indicate a read-only browser action (always allowed).
 */
const BROWSER_READONLY_PATTERNS = [
  "browser_navigate",
  "browser_view",
  "browser_find_keyword",
  "browser_scroll",
  "browser_move_mouse",
];

/**
 * Determine if a tool requires approval before execution.
 */
function requiresApproval(toolName: string): boolean {
  if (APPROVAL_REQUIRED_TOOLS.has(toolName)) {
    return true;
  }
  // Browser click/input/submit actions require approval
  if (toolName.startsWith("browser_") && !BROWSER_READONLY_PATTERNS.some((p) => toolName.startsWith(p))) {
    return true;
  }
  return false;
}

/**
 * Write a JSON line to the skill audit log.
 */
async function writeAuditLog(entry: Record<string, unknown>): Promise<void> {
  try {
    const stateDir = process.env.OPENCLAW_STATE_DIR?.trim() || path.join(os.homedir(), ".openclaw");
    const logDir = path.join(stateDir, "logs");
    await fs.mkdir(logDir, { recursive: true });
    const logFile = path.join(logDir, "skill-audit.log");
    const logLine = JSON.stringify({ ...entry, timestamp: new Date().toISOString() }) + "\n";
    await fs.appendFile(logFile, logLine, "utf-8");
  } catch (err) {
    // Never let audit logging break the agent
    console.error(
      "[samscloud-skill-audit] Failed to write audit log:",
      err instanceof Error ? err.message : String(err),
    );
  }
}

/**
 * Main hook handler — fires on agent and command events.
 */
const skillAuditHandler: HookHandler = async (event) => {
  // Only handle agent events (tool invocations)
  if (event.type !== "agent") {
    return;
  }

  const action = event.action;
  const sessionKey = event.sessionKey;
  const toolName = (event.context.toolName as string) ?? "unknown";

  // --- PRE-TOOL: Log intent and check permission ---
  if (action === "tool:pre" || action === "bootstrap") {
    const needsApproval = requiresApproval(toolName);

    await writeAuditLog({
      phase: "pre",
      action,
      tool: toolName,
      sessionKey,
      requiresApproval: needsApproval,
    });

    // If this tool requires approval, add a warning message to the event
    // so the agent knows to check approval status before proceeding.
    if (needsApproval) {
      event.messages.push(
        `[Samscloud Permission Policy] Tool "${toolName}" requires approval_status = approved before execution. ` +
        `Verify the skill_run record in Supabase has been approved before invoking this tool.`,
      );
    }
    return;
  }

  // --- POST-TOOL: Record outcome ---
  if (action === "tool:post") {
    const status = (event.context.status as string) ?? "unknown";
    const isEmailSend = toolName === "send-brevo-email" && status === "success";

    await writeAuditLog({
      phase: "post",
      tool: toolName,
      sessionKey,
      status,
      flaggedForMemoryIngestion: isEmailSend,
    });

    // If a successful email was sent, notify the agent to trigger memory ingestion
    if (isEmailSend) {
      event.messages.push(
        `[Samscloud Memory] Email sent successfully. ` +
        `Call memory.learn() with the skill_run and engagement signals to update the memory system.`,
      );
    }
  }
};

export default skillAuditHandler;
