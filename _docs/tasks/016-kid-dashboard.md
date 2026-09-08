## Goal

Kid Mode provides a child-scoped dashboard alongside the shared board.

## Acceptance criteria

- [ ] A namespaced GET dashboard is available only in Kid Mode with an active
      child; Parent Mode and missing-child sessions receive the existing mode
      warning and no data is exposed.
- [ ] The dashboard shows the active child's non-approved assigned chores and
      available/returned chores that the child may claim, including shared and
      unassigned work.
- [ ] It shows the active child's derived unpaid balance using the existing
      balance service, never a stored or recomputed duplicate.
- [ ] It shows only the active child's pending chore requests, approved
      completion history, and payout history.
- [ ] The page is read-only and does not add leaderboard, ranking, or other
      cross-child comparison data; actions remain on the shared board.
- [ ] Focused tests cover mode protection, child scoping, available/shared
      eligibility, balance composition, request history, and payout/history
      isolation.

## Out of scope

- New claim/complete behavior or dashboard-specific mutations.
- Cross-child leaderboard/ranking.
- Parent dashboard or authentication.

## Constraints

- Compose existing services/query rules from prior tasks.
- Use the current `kid_mode_required` boundary and `request.acting_child`.
- Keep the route GET-only and commit the completed task before GitHub QA.
