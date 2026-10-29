# Permanent run delegation and unattended operation

Owner direction, 2026-10-28: “Yes and make note to fix it permanently.”
This answered the explicit request to delegate this run's derived tool-policy
signing and reviewed surface classification to its existing Validator, while
preserving exact scopes, forbidden actions, required human approvals and Signet.

## Observed mismatch

The P-0001/H9 authority model permits recorded agent decisions, but legacy
`tool_policy.verify_tool_policy` and `criticality._effective_class` accepted only
human identities. The former also governs actual tool admission; changing it is
not merely verdict bookkeeping. Treating the service-identity denylist as an
allowlist would grant authority to the wrong class of identities.

The current bounded support verifies an owner `activate-policy` receipt against
the existing pinned genesis, run, enrolled Validator identity, exact tool-policy
digest and reviewed criticality-profile digest.
It does not change human signoff-required authorizations, genesis, global permissions,
Signet or other runs.
The receipt expires under the existing authority verifier. Changed subjects need
a newly bound receipt under applicable owner authority; they cannot silently
reuse the old grant. Raw promotion-input delegation allowlists are ignored.

## Permanent supported workflow: acceptance criteria

- Represent explicit delegation in the normal intake/configuration workflow,
  separating the agent's authorship from the owner's source of authority.
- Reuse retained owner intent and reviewed scope without redundant confirmations
  for the same authorized work; surface an actual expansion before activating it.
- Positively identify the intended role. Never infer grants from exclusion lists,
  model names, human aliases, self-reported metadata or a generic agent label.
- Preserve unknown-tool denial, Verboten rules, scope and expiry checks, genuine
  human approvals where required, provenance, subject binding, side effects and
  Critical fallback for missing or invalid classification.
- Prepare applicable closure evidence during the run, rather than discovering
  missing producer/integration requirements only after product acceptance.
- Verify permitted continuation and rejection of wrong run, actor, subject,
  expired authority and unauthorized privilege expansion with focused checks.

## Unattended/headless acceptance requirement

Ordinary authorized runs and recoveries must not need a local keyboard or desktop
click. Any unavoidable authentication or permission decision needs a supported
remote handoff. Verify and document vendor-specific limitations before moving to
the new workstation; do not assume every provider supports unattended recovery.

Observed obstacles to cover: CLI folder/hook trust prompts, retained-session
selection, terminal environment inheritance, expired login, permission review
handoffs and recovery that confuses a recorded disposition with actual process
state. Never solve these by disabling safeguards, broadening credentials or
claiming an unavailable remote approval path works.

This follow-up does not authorize global agent authority, new paid services,
credentials or a new control architecture. Current priority remains the accepted
application's closure and deployment.
