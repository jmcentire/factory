# DIRECTIVES — the founder directive ledger

**Per-project by construction.** The factory is generic; the target is data — so
every target project carries its *own* `DIRECTIVES/` ledger at its root (this
copy exists because the factory dogfoods itself as a target). The founder's
hardware key is per-founder, not per-project: one key signs many ledgers; the
ceremony below runs once per project ledger. `DIRECTIVE_LEDGER` points the
harness at a nonstandard location when needed.

This directory is its own git repository (nested; the factory repo ignores it).
It holds two chains:

- `ledger.jsonl` — the signed chain. Verbatim founder text only, append-only,
  hash-chained, qualifier-preserving supersession. Written via
  `harness/directive.py append|supersede|ratify`, committed **signed**.
- `provisional.jsonl` — the agent-appendable side chain (control 1a). Live
  rulings cited to the transcript (`file:line:uuid:line-sha256`), TTL'd,
  settled at the next ceremony by signed ratify/refuse entries.

## Founder ceremony (once, hardware key required — this step is yours alone)

```bash
cd DIRECTIVES
git init
git config commit.gpgsign true
git config user.signingkey <your-hardware-key-id>   # key lives on hardware requiring touch
# first signed commit = genesis; every append is committed signed
```

The agent runs `harness/directive.py verify --sigs` at grounding; an entry you
didn't physically touch a key for is not a directive. A fresh machine without
your public key fails `verify --sigs` closed — that is correct behavior; the
keyring install is part of machine setup, not an error to route around.

## Backfill

`mea_founder_directives.md` (266 extracted founder directives) is the initial
corpus, entered via `directive.py append` and committed signed in batches.

Until the ceremony has run, the chains verify hash-integrity only
(tamper-evident, honestly labeled — not tamper-proof). `DIRECTIVE_REQUIRE_SIGS=1`
turns on the signature requirement once the ceremony exists.
