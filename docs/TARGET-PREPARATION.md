# Prepare an existing repository target

`factory prepare-target` supplies first-run **data**, not permission. It runs deterministic local
qualification probes against the installed complete-tree snapshot and Git-baseline/diff machinery,
then prepares an ordinary target-manifest/2 and a nonempty built-in pattern catalog. It never
contacts the target repository, opens a run, creates signing keys or statements, or launches agents.

```bash
factory prepare-target \
  --target-id widget-docs \
  --repository-url https://example.invalid/acme/widget.git \
  --ref main \
  --subpath docs \
  --compliance-rules-path compliance/rules.json \
  --output-dir /absolute/existing-parent/widget-docs-target
```

The output directory must be absent or exactly match a previously prepared bundle; its parent must
exist. The command refuses symlink paths, linked/special files, foreign files, altered data, and
stale source or runtime identities. An exact rerun executes the probes again, revalidates every
output byte, and returns `reused: true` without rewriting. A stale bundle needs a new output path
and the normal authorization for the new manifest digest; it is never silently upgraded.

URLs must be credential-free HTTPS or SSH URLs without userinfo, query, or fragment. Supply an
explicit ref or object id; shell-like/revision-expression input is refused. Subpaths and compliance
paths must be canonical relative paths without traversal or Git control directories. The compliance
path is recorded as caller-provided target data, not invented or verified against an uncontacted
repository. Its existence and meaning remain part of target resolution/research.

The command emits:

- `target-manifest.toml`: the real catalog digest, `construction_modes = ["regenerate"]`, and
  `review_baseline = "git-reference"`.
- `pattern-catalog.json`: the versioned `complete-tree/1` pattern, with exact construction artifact
  and qualification payloads embedded for retained verification.
- `construction-artifact.json`: actual production source/dependency bytes and their SHA-256
  addresses. The conservative closure includes both installed production packages and their schema
  files, so unrelated production changes also require fresh qualification.
- `qualification-report.json`: actual probe observations, bound to that artifact and the Python/Git
  binary identities, versions, and platform. The report is deterministic; every invocation executes
  the probes rather than trusting an old `passed` flag.

JSON stdout says `status: "prepared"` and supplies manifest/catalog/report paths and digests. It
makes no claim that a target, agent, model, or future trajectory is qualified. Qualification covers
complete add/modify/delete deltas, preservation of an unchanged file, retained snapshot verification,
and refusals for wrong candidate addresses, rehashed baseline substitution, symlinks, and unsupported
brownfield output. All fixtures are disposable local repositories; no existing target data is used.
A failed probe publishes no prepared manifest or catalog. Before generation and frozen replay,
the runtime rechecks the built-in payload closure, source/tool identities, and required probe
inventory, then executes the probes again and compares the entire actual report. Recomputing a
digest over invented observations cannot substitute for those observed outcomes.
The evidence is locally executed and content-addressed, not a hardware attestation of execution.

## Complete output, small change

A small correction can use `regenerate` when the candidate is the **complete resulting target
subtree**, including its unchanged files. The host derives the full baseline-to-candidate change
set. An omitted file is a deletion; this profile never interprets sparse output as an overlay.
Existing source symlink/special-file restrictions still apply. Choose a narrow subpath when that is
the real authorized surface; do not relabel an incomplete output as complete.

`--max-attempts` defaults to 4. The signal deadline equals that ceiling, the warning pass is one
pass earlier (at least 1), and the elapsed-time backstop is 24 hours. Optional `--max-files`,
`--max-file-bytes`, and `--max-total-bytes` declare target projection limits in the existing ABI;
undeclared limits retain runtime defaults. They do not raise unrelated runner-protocol or review
changed-file ceilings. Git-reference baseline storage still requires the run-owned object store on
verification, and embeds only changed baseline file bytes under the existing review limits.

## Continue through existing gates

Use `factory inspect-target --manifest <manifest_path>`, then the ordinary Stage R request and
recorded human statement, resolution, distinct Stage E authorization, and externally bound resume
checkpoint. Preparation grants none of those authorities and does not bypass runner qualification,
phase ratification, independent Tester isolation, acceptance, review, or promotion.

The plan references `complete-tree/1` and its exact catalog-entry digest with configuration
`{"artifact_directory":"artifact"}`; Product/Architecture coverage and Operational oracle links
are still mandatory. The default generic adapter names are existing registry selections, not
new integrations or permission grants. The built-in payload verifier does not claim that external
catalog artifact retrieval, which remains a separate unwired seam, is repaired.

### Declare target verification before Stage R

The prepared bundle intentionally contains no invented test/build command. Keep its four files
unchanged for exact revalidation. Copy `target-manifest.toml` to the target configuration location,
then have the Validator select the actual project verification argv from its repository docs.
The later ratified strategy must cover those commands; changing them requires fresh target
authorization. Set the existing `[build]` fields `test_command = ["executable", "literal-arg"]`
and, when needed, `build_command = ["executable", "literal-arg"]` to those real commands. These
example strings describe the shape; they are not verification commands. Reinspect the resulting
manifest and use its new raw-byte digest for Stage R. Never edit retained authorized manifest bytes.
The catalog digest remains the address of the unchanged prepared construction catalog.

Endgame requires `build.test_command`, runs optional `build.build_command` first, then tests, with
literal argv and no shell interpretation or ambient command selection. Known Make dry-run/include,
pytest selection/plugin, and shell-startup controls are stripped from the target execution
environment; other target environment values and tool PATH retain the existing trust model. Both
target and host receipts use the pinned system Bash interpreter. Working directory is the fresh
candidate's authorized subpath. Missing command, retained-manifest substitution, unsafe cwd,
nonzero build/tests, or observed target/host source drift prevents admission.

Factory's real host `make test-isolation` runs from its own source tree with its pinned interpreter
before any target verification command. The host uses a minimal explicit environment rather than
inheriting tool, shell, Make, pytest, or Xcode selectors. Make is resolved from the trusted system
tool path (through the system Xcode selector on macOS), then executed by its exact absolute path.
The retained plan binds that environment and the actual executable bytes/modes for Make, its
selector, Bash, and Python, plus the host proof source closure (receipt, Makefile, pytest
configuration, conftest, runtime, and fixture sources), separately from the target candidate.
Source identities are rechecked
before and after host proof and before admission; they detect observed drift, not transient
edit-and-restore attacks or a compromised host verifier. Target test processes remain within the
existing harness execution trust model; this routing change does not claim new write confinement.
The host proof checks backend capability and Coder/Tester integration; it neither qualifies this
run's actual lanes nor upgrades tmux author mode from its explicitly unqualified coordination mode.
Current-run runner qualification/receipts, discovery, independent acceptance, live proof, resource
hygiene, and all other existing promotion requirements remain separate mandatory predicates.

Target-specific live-proof configuration remains necessary. Brownfield partial-output review stays
refused. A prepared manifest/catalog is not a run-ready claim.
