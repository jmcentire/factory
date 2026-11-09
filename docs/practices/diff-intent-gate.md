# Practice — review changes against intent

Compare a change with the user's current request and the repository contracts it
operates under. The author’s explanation is evidence about an approach, not authority
to change expected behavior.

1. Identify the affected requirement, invariant or permission boundary. Inspect changes
   to role ownership, scope, prohibitions and failure behavior alongside implementation.
2. Trace the source when a requirement is uncertain: the current user request and
   clarifications, public contracts and relevant Git history. An old implementation or
   document does not override a later user instruction.
3. Distinguish an intended change from an accidental side effect or an agent-invented
   requirement. State the concrete before/after behavior and its consequence.
4. The Validator resolves routine interpretation within authorized scope, records
   consequential decisions and preserves independent author inputs. Ask the user about
   material ambiguity in the requested outcome, a consequential tradeoff or an action
   outside existing authority; continue independent work while it is unresolved.
5. Review the candidate and meaningful regression evidence. Preserve actual platform
   permissions and report unchecked assumptions or failures accurately.

The same reasoning applies to workflow and instruction changes. A document diff is not
a reason to recreate an approval prerequisite that the user has already removed.
