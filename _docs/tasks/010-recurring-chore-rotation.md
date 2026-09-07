## Goal

Parent Mode can configure a recurring chore with a schedule and an ordered
rotation of children. Completing and approving one occurrence creates the next
scheduled occurrence for the next child in the rotation.

## Acceptance criteria

- [ ] The parent chore editor can create and edit a recurring chore with a
      daily, weekly, or custom schedule, a positive interval, and an optional
      custom weekday list.
- [ ] The editor can submit an ordered rotation of distinct children; invalid
      child ids, duplicate children, malformed positions, and an empty custom
      weekday schedule are rejected without partial database writes.
- [ ] A recurring chore stores one `RecurrenceRule` and ordered
      `RotationSlot` rows, while a one-off chore keeps `recurrence_rule=None`.
- [ ] The first recurring occurrence is assigned to the first rotation child;
      an empty rotation is allowed but does not generate reassigned
      occurrences.
- [ ] A recurring occurrence's approval generates exactly one next occurrence
      with the same chore details, the next scheduled due datetime, and the
      next child in rotation. The source occurrence remains approved.
- [ ] Daily schedules advance by `interval` days, weekly schedules by
      `interval` weeks, and custom schedules advance to the next selected
      weekday while respecting the interval.
- [ ] Rotation position advances transactionally and wraps fairly across at
      least two complete cycles; repeated processing of the same occurrence is
      idempotent.
- [ ] Recurrence generation is available through a small service/management
      entry point so tests and scheduled jobs can process due approved
      occurrences without duplicating business rules in views.
- [ ] Focused tests cover form validation, persistence, schedule advancement,
      fairness, idempotency, and rollback/no-partial-write behavior.

## Out of scope

- Reminder delivery or a real background scheduler — Issue 14.
- Approval UI and payout workflows — Issue 11.
- Authentication or real parent accounts — the MVP explicitly excludes them.

## Constraints

- Reuse the existing `RecurrenceRule`, `RotationSlot`, and `Chore` models;
      add only the smallest schema fields needed to make schedule processing
      unambiguous.
- Keep the state machine as the only writer of chore status transitions.
- Use atomic transactions and row locks around rotation advancement.
- Preserve the parent-only editor boundary and existing one-off behavior.
- Commit the completed task before GitHub QA.
