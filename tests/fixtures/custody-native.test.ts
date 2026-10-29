import { expect, test } from 'claude-code/testing';

// The installed host runs these modules, but process execution is stubbed:
// no real Signet call, policy change, model turn, credential or network is involved.
test('native module retains a deny before the next normally permitted call', async ($, on) => {
  let held = false;
  let underlying = 0;
  let evaluations = 0;
  on('process.run', (_$, e) => {
    const payload = JSON.parse(e.init.stdin);
    let decision = 'allow';
    if (held) decision = 'deny';
    else {
      evaluations++;
      if (payload.tool_name === 'Bash') { held = true; decision = 'deny'; }
    }
    return { value: { exitCode: 0, stdout: JSON.stringify({ hookSpecificOutput: {
      hookEventName: 'PreToolUse', permissionDecision: decision,
      permissionDecisionReason: decision === 'deny' ? 'synthetic retained hold' : '',
    }}), stderr: '' }};
  });
  on('tool.call', () => { underlying++; return {result: 'synthetic success'}; });
  await $.tool.call({ tool: 'Read', file_path: '/synthetic-context' });
  const denied = await $.tool.call({ tool: 'Bash', command: 'synthetic' });
  const after = await $.tool.call({ tool: 'Read', file_path: '/different-context' });
  expect(denied.isError).toBe(true);
  expect(denied.text).toBe('synthetic retained hold');
  expect(after.isError).toBe(true);
  expect(after.text).toBe('synthetic retained hold');
  expect(underlying).toBe(1);
  expect(evaluations).toBe(2);
});

test('a process failure is closed without invoking the next native hook', async ($, on) => {
  let underlying = 0;
  on('process.run', () => ({ value: { exitCode: 77, stdout: '', stderr: '' }}));
  on('tool.call', () => { underlying++; return {result: 'synthetic success'}; });
  const result = await $.tool.call({ tool: 'Read', file_path: '/synthetic' });
  expect(result.isError).toBe(true);
  expect(result.text).toBe('Factory policy custody unavailable; action blocked.');
  expect(underlying).toBe(0);
});
