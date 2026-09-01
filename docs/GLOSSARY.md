# Glossary — the declared definition site

Ruling 5a (781f78d80045): each entry anchors to a canonical code referent —
`module.py::symbol` at a recorded source digest — so a definition goes STALE the
moment its referent changes and `make check-glossary` says so. Every other
surface links here; a second definition site anywhere scanned is red
(single-definition-site, exact-token). Neither check is paraphrase policing:
that residual keeps its true defense (fewer surfaces plus the doctrine guard's
denylist), and nothing makes it impossible — stated, not hidden.

Entry format (machine-parsed): `- **term** — definition. Referent:
`module.py::symbol` @ sha256:<digest of the symbol's source segment>`.
To renew a stale entry: re-read the symbol, update the definition if the
behavior moved, re-derive the digest.

- **promotion decision** — the pure fail-closed answer to "may this exact built artifact be promoted?", computed from evidence, authority, criticality, and host-derived disturbed surfaces; it never performs the promotion. Referent: `factory_core/promotion.py::decide_promotion` @ sha256:5ce461bb0e95c4e2fa9dd0a34ac623dab98773b29a95c86bbbb022e82988ddfe
- **coverage map** — the closed, ratified enumeration of territories, adequacy criteria, and verbs the verdict layer computes from; prose is never an input to it. Referent: `factory_core/verdict.py::CoverageMap` @ sha256:7d62de3d67e18d1c7f0e2631602a5a7a10dcbb6f99ade9a39ec219620f3fb55c
- **evidence ledger** — the append-only, content-addressed, hash-chained, SoD-enforcing transition record; keyed entry addresses (hmac-sha256) make whole-history rewrite require the host-held key. Referent: `factory_core/manifest.py::Ledger` @ sha256:d4c61c532a751c70329800052b20bff814f78d22b16b5be3399a3e116b8cbe46
- **chain key** — the per-ledger HMAC key derived from founder-root-recoverable material at the durability seam; absent material is the loud migration-only unkeyed mode. Referent: `factory_runtime/durability.py::load_chain_key` @ sha256:d2a0e6c374abb7b7b4c2e01cc5cf6429bea0c5e31aaa0bee7ae828f5eea82901
- **feasibility preflight** — the early NO at intake: a pure tri-state verdict (hard NO, disclosure, could-not-check-loud) over ratified facts, so every reason a run would die surfaces at hour zero. Referent: `factory_runtime/preflight.py::run_preflight` @ sha256:6ea58dfb969e7e99df907456f55f5ea2f16990f278c42c34c38136d1f9e5e843
- **transition admission** — the schema-version-keyed frozen rows both state paths consume, migrated axis by axis so the write/derive drift class stays dead. Referent: `factory_runtime/transition_admission.py::allowed_authority_nonce_counts` @ sha256:bf2d27ef202c07ca006b9bc5dc332e24eed53414ffbc2983863b46a7d15c367a
- **repair ceremony** — the only exit from a wedge: one bounded operator action applying a pre-signed adjudication, leaving a verifiable state and a persisted signed record. Referent: `factory_runtime/repair_ceremony.py::apply_chain_repair` @ sha256:4f10e1fd431916e75b23b254821fe1d2eb4b86d67c5ca076f1b381c75262fe44
- **run store** — the persisted lifecycle ledger with head-pinned load: a pinned projection whose self digest and ledger tail verify skips the derive walk; every verification-bearing state takes the full walk. Referent: `factory_runtime/state.py::RunStore` @ sha256:083c6628b69fb74665014a705b7c25220c7910c34808777a764eae0657a560db
