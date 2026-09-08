## Goal

Approval warns a parent when a child's approved rewards for the current
calendar week reach or exceed that child's optional earning limit, without
blocking the approval.

## Acceptance criteria

- [ ] The existing optional non-negative `Child.weekly_earning_limit` remains
      the single source of the configured cap; no duplicate stored balance is
      introduced.
- [ ] Weekly earned rewards are derived from approved rewarded chores whose
      `approved_at` falls in the current local Monday-through-Sunday week.
- [ ] Approving a chore with no limit or no reward behaves exactly as before
      and emits no limit warning.
- [ ] Approving a rewarded chore that brings the child's projected weekly
      total to or above the limit emits a clear Django Admin warning showing
      the child, projected amount, and configured limit.
- [ ] The warning is soft: the chore is still approved and existing balance,
      recurrence, and state-machine behavior are unchanged.
- [ ] Rewards approved in a prior or future week do not affect the current
      week's warning calculation.
- [ ] Focused tests cover no-limit/unrewarded cases, current-week aggregation,
      boundary/over-limit warnings, week isolation, and successful approval.

## Out of scope

- Blocking approvals or enforcing a hard earning cap.
- The weekly family summary screen — Issue 15.
- Payout recording/correction — Issues 25 and 26.

## Constraints

- Keep the calculation derived and read-only; do not add a running counter.
- Reuse `Chore.approve()` and the existing Admin action.
- Use timezone-aware local-week boundaries and Decimal money arithmetic.
- Commit the completed task before GitHub QA.
