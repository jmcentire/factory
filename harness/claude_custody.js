// Rendered with an immutable argv by claude_custody.py. No shell or tool-input logging.
// A settings hook is downstream of native modules: a native DENY can skip it entirely.
// This module precedes the existing Signet module by an explicit manifest dependency.
const argv = __FACTORY_CUSTODY_ARGV__;
const unavailable = 'Factory policy custody unavailable; action blocked.';

export function register(on) {
  on('classic.PreToolUse', async ($, e, next) => {
    let decision;
    try {
      const {tool, tool_use_id, ...tool_input} = e;
      if (typeof tool !== 'string' || !tool) return {deny: unavailable};
      const output = await $.process.run(argv, {
        stdin: JSON.stringify({hook_event_name: 'PreToolUse', tool_name: tool, tool_input}),
        timeoutMs: 24000,
      });
      if (output.exitCode !== 0) return {deny: unavailable};
      const value = JSON.parse(output.stdout);
      if (!value || Object.keys(value).some(k => k !== 'hookSpecificOutput')) {
        return {deny: unavailable};
      }
      decision = value.hookSpecificOutput;
      if (!decision || decision.hookEventName !== 'PreToolUse' ||
          Object.keys(decision).some(k => ![
            'hookEventName', 'permissionDecision', 'permissionDecisionReason',
            'updatedInput', 'additionalContext',
          ].includes(k))) return {deny: unavailable};
      for (const key of ['permissionDecisionReason', 'additionalContext']) {
        if (key in decision && typeof decision[key] !== 'string') return {deny: unavailable};
      }
      if ('updatedInput' in decision && (!decision.updatedInput ||
          typeof decision.updatedInput !== 'object' || Array.isArray(decision.updatedInput))) {
        return {deny: unavailable};
      }
      if (decision.permissionDecision === 'deny') {
        return {deny: decision.permissionDecisionReason || 'Factory policy denied this action.'};
      }
      if (decision.permissionDecision === 'ask') {
        return {ask: decision.permissionDecisionReason || 'Factory policy requires scoped approval.'};
      }
      if (decision.permissionDecision !== 'allow') return {deny: unavailable};
    } catch { return {deny: unavailable}; }
    // ALLOW never replaces the other modules, their redaction, or the host's own checks.
    const forwarded = decision.updatedInput ? {
      ...decision.updatedInput, tool: e.tool, tool_use_id: e.tool_use_id,
    } : e;
    const result = await next(forwarded);
    return decision.additionalContext ? {
      ...result, additionalContext: [...(result.additionalContext ?? []), decision.additionalContext],
    } : result;
  }).catch(() => ({deny: unavailable}));
}
