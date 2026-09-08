## Goal

Kids can submit a chore request in Kid Mode, and a parent can accept or
decline pending requests from Django Admin.

## Acceptance criteria

- [ ] Kid Mode exposes a normal form for a required title and optional notes.
- [ ] A valid submission creates exactly one pending `ChoreRequest`, credits
      it to the active session child, and redirects with a success message.
- [ ] The request view never trusts a posted child id and cannot be used from
      Parent Mode or without an acting child.
- [ ] Parent Admin lists requests with requester/status information and
      exposes accept and decline actions for pending requests.
- [ ] Accepting a pending request atomically creates one available `Chore`
      assigned to the requesting child, links it through `resulting_chore`,
      and marks the request accepted.
- [ ] Declining a pending request creates no chore and marks the request
      declined; repeated processing of an already decided request is safe and
      reported as a failure rather than duplicating data.
- [ ] Focused tests cover form validation, active-child attribution, mode
      protection, accept, decline, and idempotency/no-duplicate behavior.

## Out of scope

- Request editing or parent comments/reasons.
- Automatic request expiration or notifications.
- Authentication design — Issue 28.

## Constraints

- Use the existing `ChoreRequest` statuses and `resulting_chore` relation.
- Keep accept/decline writes in a small atomic service used by Admin actions.
- Preserve the current Parent/Kid mode boundary and no-dependency rule.
- Commit the completed task before GitHub QA.
