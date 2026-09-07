## Goal

Parents can process completed chores efficiently in Django Admin while every
approval and rejection still goes through the domain state machine.

## Acceptance criteria

- [ ] `ChoreAdmin` exposes status, assigned child, category, priority, due
      date, and reward in the list, with filtering by status/child/category,
      title/notes/child search, and bounded pagination.
- [ ] "Approve selected" applies `Chore.approve()` to selected awaiting-
      approval chores, preserving the existing status transition and balance
      behavior; invalid rows are reported without crashing the changelist.
- [ ] "Reject selected" first renders a confirmation form requiring a
      non-empty rejection reason; no rows are changed while the reason is
      missing.
- [ ] Submitting a valid rejection reason applies `Chore.reject(reason)` to
      selected awaiting-approval chores, stores the stripped reason, and
      returns each chore to `returned` for re-claiming.
- [ ] Approval and rejection actions report success/failure through standard
      admin messages and do not directly assign chore status or duplicate
      balance/recurrence logic.
- [ ] Admin action tests cover list configuration, filtering/search metadata,
      approval, rejection validation, valid rejection, and invalid-state
      handling.

## Out of scope

- A custom parent-facing approval screen outside Django Admin.
- Authentication design or admin-user onboarding — Issue 28.
- Payout recording and correction workflows — Issues 25 and 26.

## Constraints

- Keep the existing state machine as the only transition writer.
- Preserve append-only payout rules and the recurrence approval hook.
- Use normal Django Admin action/form behavior, with no new dependency.
- Commit the completed task before GitHub QA.
