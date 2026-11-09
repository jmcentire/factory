# Design evidence for independent roles

The current responsibilities and operating rules live in
[SOFTWARE-FACTORY.md](SOFTWARE-FACTORY.md). This file retains the design evidence that
informs the choice of independent author inputs and resident oversight.

| Reference | Design basis | Limitation |
|---|---|---|
| E1 | Cross-model diversity can reduce correlated misreading. | Model families are descriptive; explicit user assignments govern. Diversity does not prove independence or semantic correctness. |
| E3 | Repeated interpretation and re-delegation can lose intent; independent work against the same public contract reduces that risk. | The Validator still interprets intent and judges the result. Separate inputs and Orchestrator oversight reduce, but do not eliminate, that limitation. |

These bases came from the founder's multi-agent research and the project's recorded
experience with correlated failure and interpretation loss. This document does not claim
a new empirical result or a universal quantitative guarantee. Revisit the design when
new evidence supports a better arrangement.

The practical implementation is asymmetric Coder/Tester input, private author context,
Validator-owned acceptance and an independently resident Orchestrator. Actual tests and
observations establish the behavior of a candidate; role labels alone do not.

## Referenced design principles

These identifiers retain the references used by the testing standard. Their operational
application is defined by the current workflow and standards.

| Reference | Principle |
|---|---|
| S4 | The user's instructions govern intent. Roles do not invent permission or redefine requested behavior to fit their work. |
| I3 | Coder and Tester work from separate inputs and public contracts, with no direct channel or cross-reading of private work. Validator feedback preserves that separation. |
| I5 | Criticality and oracle adequacy are distinct. An unresolved critical coverage gap blocks acceptance of that behavior; other work receives proportionate checks and an honest account of limitations. |
| I14 | An expected outcome changes when its governing requirement changes. A mistaken test derivation or fixture is corrected from the contract with its reason preserved, not to make a candidate pass. |
