### Open the PR ready for review, then clear every comment before the verdict

The Validator owns the PR; the Coder hands you a branch. Once you have it, and before
`endgame.sh`, open the PR ready for review, never as a draft: review bots such as Adapt
skip drafts, so their comments would otherwise land once a reviewer is already looking.
If `gh label list --repo <owner/repo> --search Factory` shows no `Factory` label, create it first with
`gh label create Factory --repo <owner/repo> --color 5319e7 --description "PR opened by the Factory"`.
Push the reviewed branch from the run worktree (`git -C <work> push origin HEAD:<branch>`),
never from the human's checkout. Write the title and body by the Validator prompt's
PR-description rules, then open it:
`gh pr create --repo <owner/repo> --base <base> --head <branch> --label Factory --title <title> --body-file <file>`.

1. After the PR opens and after every push, note the head SHA and wait for CI
   (`gh pr checks <n> --repo <owner/repo> --watch`) and for the review bots to review that
   SHA. A push does not always retrigger Adapt: if no Adapt review lands on that SHA, a PR
   comment of only `@adaptcom review` asks for one (once per commit per 10 minutes), but it
   posts under the human's account, so only with their OK; or the human clicks the Re-run
   link in its last review. If neither happens, or the review still does not land, that
   bot cannot be triggered (see below).
2. Collect every finding on that SHA: each unresolved review thread, bots and outdated
   threads included (`gh api graphql` on `pullRequest.reviewThreads(first: 100)`, keeping
   `isResolved == false` and paging with `after` while `hasNextPage`), each bot's latest
   review body, which can hold findings no thread does, and each red check. A failed query
   is not zero findings.
3. Send each real finding, and each red check, back as a repair to the lane that owns it
   (the Tester for a finding in its tests or test setup, otherwise the Coder), one finding
   per file and nothing from the other lane. Reproduce a red test check from the
   implementation's own output first: if you cannot, it goes to the Tester; if you can, the
   Coder gets the requirement id and the behaviour you saw, never the test, its name or log: `harness/tmux_lane_message.sh <run> validator <slot> review --verdict repair --review-file <file>`.
   Never write the fix yourself. Judge the repaired candidate, push it, and resolve the
   thread only once its fix is pushed. Never resolve a thread just to clear the count.
4. If you disagree with a finding, or a bot re-raises one already fixed or disputed in
   this run, do not repair it again: draft a one- or two-line reply for the human. Never
   post as them. Neither a re-raise nor a disagreement counts as a round. If the human sides
   with the bot, it is a real finding: repair it as in step 3, and that repair is a round.
5. Stop after three repair rounds.

A thread is resolved when its fix is pushed, or, for a disputed or re-raised finding, once
the human has posted your drafted reply; a reply never clears a finding you accept. Run the
endgame only when, on the final pushed SHA, the bots have reviewed it, every check is green
with none pending (`gh pr checks <n> --repo <owner/repo>` run immediately before; if any is
pending, wait and run it again) and unresolved threads are 0. Anything else still open (an accepted finding not yet fixed, a reply only
the human can post, a bot you could not trigger) is an impasse only the human can clear:
give them the PR, the head SHA, each open thread with its drafted reply and the round
count, and wait. Once every condition above holds, run the endgame on that SHA and tell
the human the PR is ready to send to reviewers:

```bash
harness/endgame.sh <run> <final-sha> --repo "<work>" -- <the project's relevant check command>
```

Endgame retains the command, output and exit code for that exact commit; choose the
project's actual checks. You judge the behavior, coverage and remaining risk and render
the verdict; passing checks are evidence, not acceptance. Merge requires the human's explicit approval, which may authorize the agent to merge. Recheck the PR head,
Factory label, CI and unresolved findings immediately before merging, and merge only
that approved SHA. The verdict covers only its SHA: a later push needs a fresh judgment.
