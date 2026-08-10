# reconcile.d — declared-vs-live probes (control 9)

Executable probes, one per substrate concern the current objective touches.
`ground.sh` step 6 runs every executable file here; any non-zero exit blocks
grounding (declared/live drift is treated exactly like channel drift).

The harness owns the requirement and the receipt; the target owns the probe.
Examples of what belongs here, per target:

- `iam-terraform-vs-live` — diff declared IAM against live grants
- `config-tfvars-vs-runtime` — diff tfvars against the running service's config
- `image-digest` — assert the deployed image digest matches the expectation

A probe prints what it checked and exits 0 (reconciled) or non-zero (drift).
No probes registered means step 6 is a no-op with a visible notice — that is a
gap statement, not a pass.
