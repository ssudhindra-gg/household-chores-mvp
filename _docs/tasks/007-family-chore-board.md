## Goal

Both modes can open one shared, read-only family chore board that shows the
current chores and supports the four filters named by the MVP specification.

## Acceptance criteria

- [ ] `GET /board/` renders every chore with title, category, priority, status,
      assigned child, and reward.
- [ ] The board can filter by assigned child, category, status, and priority;
      combining filters applies all selected filters together.
- [ ] Unassigned/shared chores remain visible when no child filter is selected,
      and an unpaid chore is visibly labelled as unpaid rather than as a blank
      reward.
- [ ] An empty result set renders the board successfully with a clear empty
      state, not a server error.
- [ ] Missing or malformed filter values are ignored safely and never cause a
      500 response.
- [ ] The board is read-only in this task: a GET does not change any chore and
      the page contains no claim, complete, approve, reject, or payout control.
- [ ] Parent and Kid sessions receive the same shared board data.

## Out of scope

- Claim and complete actions or HTMX partial updates — Task 8.
- Parent chore creation/editing — Task 9.
- Derived reminder badges — Task 14.
- Kid-specific dashboard composition — Task 16.

## Constraints

- Keep the board view in `chores/views.py`, route it from the project URLs, and
      render a template under `chores/templates/chores/`.
- Use only Django ORM filtering and existing model choice sets; no new
      dependencies or schema changes.
- Use `select_related` for the assigned child and add view tests for every
      filter, combined filters, empty results, malformed input, and both modes.
