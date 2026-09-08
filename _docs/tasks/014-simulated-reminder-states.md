## Goal

The family board shows a derived reminder badge for chores that are upcoming
or overdue, without sending email or storing reminder state.

## Acceptance criteria

- [ ] A chore with a due datetime later than the render-time `now` is marked
      `upcoming`.
- [ ] A chore with a due datetime at or before render-time `now` is marked
      `overdue`.
- [ ] A chore without a due datetime has no reminder badge and remains visible
      on the board.
- [ ] The board displays the derived state as a clearly labeled simulated
      reminder badge/note in both Parent and Kid modes.
- [ ] Reminder state is computed at render time, is not a model field, and is
      not written to the database.
- [ ] Focused tests cover upcoming, overdue, no-due-date, and both-mode board
      rendering behavior.

## Out of scope

- Email delivery, notifications, or a background scheduler.
- Reminder preferences or configurable thresholds.

## Constraints

- Use timezone-aware comparisons and the existing board queryset.
- Keep the board read-only in Parent Mode and preserve Kid Mode actions.
- Commit the completed task before GitHub QA.
