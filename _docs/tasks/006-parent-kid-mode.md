## Goal

The browser session has a visible Parent/Kid mode switch, and mode-sensitive
actions cannot be performed from the wrong mode without authentication.

## Acceptance criteria

- [ ] A GET of the mode-switch page shows the current mode and controls for
      both Parent Mode and Kid Mode.
- [ ] Posting `mode=parent` or `mode=kid` changes only the current session's
      mode, and the next GET shows that mode as active.
- [ ] An unsupported mode value is rejected without changing the session.
- [ ] Approve, reject, and record-payout endpoints succeed in Parent Mode and
      return HTTP 403 without changing data in Kid Mode.
- [ ] Claim, complete, and chore-request endpoints succeed in Kid Mode and
      return HTTP 403 without changing data in Parent Mode.
- [ ] The guarded endpoints continue to use the existing state-machine and
      payout services; mode checks do not duplicate domain rules.
- [ ] A session's mode is isolated from every other browser session.

## Out of scope

- Real users, authentication, authorization, or household accounts — the MVP
  explicitly excludes these.
- The shared chore board, HTMX partials, and dashboard presentation — Tasks 7,
  8, and 16.
- Parent chore creation/editing and request accept/decline screens — Tasks 9
  and 13.

## Constraints

- Keep mode state in Django's existing session middleware; use no new
  dependencies.
- Put reusable mode checks in `chores/modes.py` and views in `chores/views.py`.
- Use POST for every state-changing endpoint and add view-level tests for
  allowed, blocked, invalid, and session-isolation cases.
