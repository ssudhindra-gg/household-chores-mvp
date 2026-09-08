## Goal

Shared chores can be claimed by several children at once. Claims are recorded
as rows, the board/admin show every claimant, and approval splits the reward
exactly across claimants while preserving the existing primary `assigned_child`
pointer for compatibility.

## Acceptance criteria

- [ ] Add `ChoreClaim` with protected child, cascading chore, ordered claim
      timestamp, optional completion timestamp, optional cent-quantized reward
      share, uniqueness per chore/child, and the required constraints/indexes.
- [ ] Add a reversible data migration: existing claimed/awaiting/approved/
      returned chores with an assigned child gain one claim; available
      pre-assignments gain none; approved claims receive the full old reward.
- [ ] Add `Chore.claimants` and `claimant_ids` with the no-claim fallback to
      `assigned_child`, and use them throughout eligibility, balances, board,
      dashboard, and admin reads.
- [ ] Allow a second claimant only on shared claimed chores; prevent duplicate
      claims and preserve the earliest claimant in `assigned_child` and
      `claimed_at`.
- [ ] Complete accepts an optional claimant, records that claimant's
      `completed_at`, and submits the one shared chore for approval.
- [ ] Release accepts an optional claimant, removes only that claim when
      multiple claims remain, and uses the existing available transition only
      when the last claim leaves.
- [ ] Reject clears every claim's completion timestamp while preserving claims.
- [ ] Store exact Decimal reward shares on approval, with remainder cents going
      to earliest claims; update derived balances to sum shares with the
      legacy no-claim fallback.
- [ ] Update board, HTMX rows, Kid dashboard, and Chore Admin to show/use all
      claimants without adding duplicate rows or cross-child leakage.
- [ ] Preserve activity-event recording and actor-mode semantics for every
      transition.
- [ ] Add focused model, migration, state-machine, reward, balance, board,
      dashboard, and admin tests; full suite, checks, and migrations remain
      green.

## Out of scope

- Per-claimant completion gating — Issue 33.
- Per-chore reward split selection — Issue 34.
- Give-back UI — Issue 20.
- Claim concurrency guard — Issue 22.
- Shared-reward updates to summary/limit modules — Issue 32.

## Constraints

- Keep `assigned_child` as the earliest-claimant compatibility pointer.
- Every claim-row write comes from the state machine or rewards service.
- Keep state-machine imports model-free and money out of the state machine.
- No new dependency; use Decimal only for reward allocation.
- Commit and push at the logical completion point before GitHub QA.
