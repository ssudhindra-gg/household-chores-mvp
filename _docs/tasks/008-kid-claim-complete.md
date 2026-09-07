## Goal

In Kid Mode, a child can claim an eligible board chore and mark their claimed
chore complete through in-place HTMX updates, while Parent Mode cannot perform
either action.

## Acceptance criteria

- [ ] A Kid Mode claim request for an available unassigned chore changes it to
      `claimed`, assigns the selected child, and returns an HTMX-compatible
      board fragment without a full-page redirect.
- [ ] A Kid Mode claim request for a pre-assigned chore succeeds only for that
      child; a different child receives a visible error and the chore is
      unchanged.
- [ ] A shared chore can be claimed by an eligible child under the same
      Kid-Mode endpoint.
- [ ] A Kid Mode complete request changes a claimed chore to
      `awaiting_approval` and returns the updated fragment.
- [ ] Claim and complete requests in Parent Mode return HTTP 403 and do not
      change the chore.
- [ ] Invalid state-machine actions return a visible error without changing
      the chore, and non-POST requests are rejected.
- [ ] The board exposes claim/complete controls only in Kid Mode and targets
      the HTMX endpoints.

## Out of scope

- Parent approvals/rejections and admin workflows — Task 11.
- Chore creation/editing and recurrence — Tasks 9 and 10.
- Authentication or identifying a real logged-in child — explicitly out of
      scope for the MVP; the request selects a child record.

## Constraints

- Use the existing state-machine methods and the reusable Kid Mode guard from
      Tasks 3 and 6; do not assign chore status directly in views.
- Use `django-htmx` for request detection and partial responses; add it to the
      project dependencies only with approval.
- Keep changes in the chore views, templates, URLs, dependency manifest/lock,
      and focused tests. No schema changes are expected.
