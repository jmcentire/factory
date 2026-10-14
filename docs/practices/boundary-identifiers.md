# Boundary identifiers: verified, never assumed

A practice for every run, enforced in Phase A, at the Orchestrator's dispatch and verdict
checkpoints, and at endgame. It exists because one defect shape kept recurring on the first
consuming target, and every instance passed every local test.

The founder's direction, verbatim (2026-10-14):

> There's a plague of issues which all have the same form. Mismatched identifiers, names,
> tokens, etc across boundaries. Orchestrator ought to be checking for this. Ensure that is
> true. Also, ensure PACT and Constrain do that test and that they get verifiable records to
> reference and don't merely assume what the key, token, value, name, etc are.

and, on the target where it surfaced:

> System 1 calls it one thing and system 2 another so the mismatches abound. ... Some worker
> is presumed to exist with access and simply doesn't. Another service is presumed to
> recognize your credentials but doesn't. Those aren't acceptable.

## The shape

A boundary identifier is a literal two parties must both recognize, written by parties that do
not share a compiler: a principal or service account, a database role, grant, table or column,
a metric or resource type, a log field, a token audience or claim, an environment variable, a
service, topic, queue, bucket, secret or header name. Each party is internally consistent, so
nothing local fails. The instances that motivated this practice, all from one production system:

| What each side said | What happened |
|---|---|
| A gateway queried revocation tables; its database role was never granted them. | Every signed-in request failed the check, the error was swallowed, and the evidence the check existed to collect was never collected. |
| An alert filtered on resource type `uptime_url`; every uptime check produced `cloud_run_revision`. | The only uptime alert could never fire. |
| A probe called a service with no credential; the service required an identity token. | Every call was refused before reaching the service and still counted as "up". |
| An uptime check targeted a service name that did not exist. | The check measured nothing. |
| Log formatters wrote the level under `level`; the platform reads only `severity`. | Every severity search and error grouping missed every application error. |
| Per-service grants said an isolated service was cut off; a project-wide invoker grant reached it. | Isolation held only in the application layer, not in the access policy in force. |

The last row is the dangerous variant: an isolation boundary that exists in the declared
configuration and not in the live one.

## The register

Each run carries `artifacts/boundary-register.json` (format `boundary-register/1`, shared with
Pact and Constrain). One entry per boundary identifier:

```json
{
  "schema": "boundary-register/1",
  "entries": [
    {
      "id": "gateway-db-role",
      "kind": "role",
      "relation": "equal",
      "description": "the gateway's database role must hold SELECT on the tables it reads",
      "sides": [
        {"party": "gateway", "role": "consumes", "value": "gateway_ro",
         "records": [{"kind": "file", "path": "gateway/db.py", "line": 12,
                      "sha256": "<64 hex of the file>"}]},
        {"party": "external:postgres", "role": "grants", "value": "gateway_ro",
         "records": [{"kind": "observation", "root": "run",
                      "path": "artifacts/boundary-observations/grants.txt",
                      "sha256": "<64 hex>", "command": "psql -c '\\dp user_sessions'",
                      "observed_at": "2026-10-14T15:27:00+00:00"}]}
      ]
    }
  ]
}
```

- **`relation: equal`**: every side's `value` is the identical literal (case-sensitive), with at
  least one side that `defines` or `grants` it and one that `consumes` or `presents` it.
- **`relation: absent`**: an isolation boundary. Exactly one `presents` side (the subject, such
  as a principal) and one or more `denies` sides whose records are the policy actually in force.
  The subject must not occur in any denying record.
- **Records** are the only acceptable source of a value. A `file` record cites a line in the
  target checkout (`root: target`, the default) or in the run (`root: run`, for example a line
  of the signed Architecture Specification that decides a literal not yet written). An
  `observation` record is the saved output of a live query, with the exact command and the
  time. Every record carries the SHA-256 of the whole file.
- A side with no record is **UNVERIFIED** and blocks. A placeholder value (`TODO`, `<project>`,
  `unknown`, `assumed`) blocks. A run that genuinely crosses no boundary says why in
  `no_boundary_basis`; an empty register without one blocks.

Build records with `python3 harness/boundary_register.py cite`, which reads the source,
computes the digest and line, and refuses when the literal is not there. Nobody types a digest
or a line number.

## What the verifier proves, and where it runs

`harness/boundary_register.py verify` re-reads every record and refuses a stale digest, a
cited line that lacks the literal, a literal found only inside a longer name (`svc-auth` inside
`svc-auth-runtime`), parties that disagree (naming the near-miss when they differ only in case
or separators), and a subject present in a record that must exclude it.

| Where | What it refuses |
|---|---|
| Phase A, `phase1_gate.sh` check H | Lanes launch only on a register that verifies against the base checkout. Every current run (one whose ignition wrote the agreement contract) must carry one. |
| Orchestrator `pre_dispatch` and `pre_verdict` checkpoints | The checkpoint is refused before the Orchestrator is asked, so no dispatch and no verdict crosses an unverified boundary. |
| Endgame | Target records re-resolve against the candidate commit. A cited file may change; a registered literal that the candidate dropped or renamed is drift and turns the verdict red. |

The verifier proves the registered literals agree with their records. It cannot prove the
register lists every boundary the system crosses. That is a judgment surface, and two roles own
it:

- **The Validator** compiles the register in Phase A from the Architecture Specification's
  trust and integration boundaries and from live observation of the substrate the run touches,
  and keeps it current as lanes surface new identifiers.
- **The Orchestrator** watches every lane for identifiers the register lacks (`§1`,
  `boundary_identifier_assumed`) and for access, credential acceptance or existence presumed
  without a record (`§2`, `boundary_presumed_access`). Both are standing adherence checks with
  jev rules. When the register exceeds the screen's evidence ceiling the rule reports
  unavailable, and the Orchestrator checks it directly.

## Rules for each role

1. **Never write a cross-boundary literal from memory.** Look it up in the register. If it is
   not there, stop and ask; the Validator records it with a record or rules it out.
2. **Presumed access is a question, not a fact.** "The account already has access" requires an
   observation of the policy in force. A refusal is evidence; a status below 500 is not health.
3. **Isolation is proven from the live policy.** A declared per-service grant does not prove
   isolation while a broader grant exists; register the boundary as `absent` against the policy
   actually read back.
4. **A stale record is re-derived, never edited to fit.** A digest mismatch means the source
   moved; read it again.
5. **The Tester's agreement tests use the registered literals.** A test that asserts a literal
   the register does not carry is asserting an assumption.
