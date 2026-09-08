## Goal

Parent Mode provides a read-only weekly family summary with each child's
approved completion count and approved reward total.

## Acceptance criteria

- [ ] A namespaced GET view and navigation link render the current local
      Monday-through-Sunday week.
- [ ] Every child appears exactly once, including children with no activity,
      with zero completions and `0.00` earned.
- [ ] Only chores with status `approved`, a non-null `approved_at` in the
      current week, and the child as `assigned_child` count toward the summary.
- [ ] Unapproved, undated, prior-week, future-week, unpaid, and unassigned
      chores are excluded from earnings; unpaid approved chores may still count
      as completed with `0.00` earned.
- [ ] The view is read-only, GET-only, and Parent Mode-only; no rows are
      mutated or stored summary counters introduced.
- [ ] Focused tests cover no activity, one child's activity, multiple children,
      week boundaries, and excluded statuses/rows.

## Out of scope

- Per-child dashboard/history — Issue 16.
- Payout deductions or balance display — Issue 23.
- Custom date ranges or export.

## Constraints

- Reuse the existing timezone-aware local-week convention and Decimal money
      values.
- Keep aggregation derived from approved chore rows.
- Commit the completed task before GitHub QA.
