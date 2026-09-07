# Shared Household Chores: MVP Backlog

Tasks reference [`household-chores-mvp-spec.md`](./household-chores-mvp-spec.md) and
[`architecture.md`](./architecture.md). Each task is sized for one session and can be handed to
someone who has only read the spec and architecture doc, not the other tasks.

## 1. Project scaffold with a passing test
Goal: Get an empty, runnable Django project in place with a working test harness.
Description: Create the Django project and a `chores` app with no models or views yet, wire up `manage.py test`, and add one trivial passing test (e.g. asserting the app loads) so the toolchain is proven before any real code is written.

## 2. Core domain models and migrations
Goal: Represent the MVP's entities in the database.
Description: Define the `Child`, `Chore`, `RecurrenceRule`, `Payout`, and `ChoreRequest` models per the architecture doc's data model section, including fields, relationships, and status choices, and generate migrations. Register them with default Django Admin (unstyled) so data can be inspected manually; no business logic or validation beyond field-level constraints.

## 3. Chore status state machine
Goal: Enforce valid chore status transitions from one place in the code.
Description: Implement `available → claimed → awaiting approval → approved`, with `returned` looping back to `available`/`claimed`, as model methods or a small service layer per the architecture doc. Add tests covering each legal transition and rejecting illegal ones (e.g. approving a chore that isn't awaiting approval).

## 4. Balance computation on approval
Goal: Correctly credit a child's unpaid balance when a rewarded chore is approved.
Description: When a chore transitions to `approved` via the state machine from Task 3, add its reward amount (if any) to the assigned child's unpaid balance. Compute each child's unpaid balance as approved rewarded chores minus recorded payouts, and add tests for chores with no reward, single approvals, and multiple approvals for the same child.

## 5. Payout recording
Goal: Let a parent record a cash payout to a child.
Description: Add a way to create a `Payout` (child, amount, date) that reduces the child's unpaid balance, validating that the amount is greater than zero and does not exceed the current unpaid balance. Payouts are never deleted or edited once created, only added, and payout history must remain queryable per child.

## 6. Parent/Kid mode switch
Goal: Provide the session-based mode toggle that gates parent-only vs kid-only actions.
Description: Add a UI control that flips a session flag between Parent Mode and Kid Mode with no real login involved, matching the spec's explicit scope. Add view-level checks (or a reusable decorator/mixin) so approve, reject, and record-payout actions require Parent Mode, and claim, complete, and request actions require Kid Mode, with tests for both allowed and blocked cases.

## 7. Family chore board (read-only view)
Goal: Show the shared household board that both modes will build on.
Description: Build a template and view listing all chores with their title, category, priority, status, assigned child, and reward, filterable by child, category, status, and priority as listed in the spec's "Suggested Screens". This task is read-only — no claim/complete/approve actions yet — and is reused by later tasks for both Parent and Kid modes.

## 8. Claim and complete actions (Kid Mode)
Goal: Let a child act on chores from the board.
Description: Add "claim" (for available or shared chores) and "mark complete" actions to the board built in Task 7, visible only in Kid Mode, driving the chore through the state machine from Task 3. Use `django-htmx` so these actions update in place without a full page reload, and add view tests confirming a child can only claim chores that are available or shared.

## 9. Parent chore editor
Goal: Let a parent create and edit one-off chores.
Description: Build a `ModelForm`-based create/edit view for chores (title, notes, category, priority, due date/time, optional reward, assignment or shared flag), visible only in Parent Mode. Validate due dates and reward amounts, and add tests for both valid submissions and rejected invalid ones.

## 10. Recurring chore configuration and rotation
Goal: Support recurring chores that rotate fairly among children.
Description: Extend the chore editor from Task 9 to attach a `RecurrenceRule` (daily, weekly, or custom schedule) and a rotation order among children. Implement the logic that automatically reassigns or regenerates a recurring chore to the next child in rotation on its schedule, with tests verifying the rotation order stays fair across several cycles.

## 11. Parent approvals via Django Admin
Goal: Give parents an efficient way to approve or reject completed chores.
Description: Customize Django Admin for the `Chore` model with "Approve selected" and "Reject selected" actions, the latter requiring a non-empty rejection reason, plus list filtering, search, and pagination on status/child/category. Approve and reject must go through the same state machine and balance logic from Tasks 3 and 4, not duplicate it, and rejecting must return the chore to the child per the spec.

## 12. Weekly earning limit warning
Goal: Surface (without blocking) when a child is approaching or over their configured weekly earning limit.
Description: Add an optional weekly earning limit field per child (if not already present from Task 2) and show a warning at approval time in the admin action from Task 11 when approving would take the child over their limit for the current week. This is a soft warning only — the spec does not require blocking the approval — and should be covered by a test showing the warning appears without preventing approval.

## 13. Chore request flow
Goal: Let children ask for chores to be added to the board.
Description: Add a form in Kid Mode for submitting a `ChoreRequest` (title and optional notes), and a Parent Mode view/admin action to accept a request (creating a real `Chore` from it) or decline it. Add tests for both the accept path (a `Chore` is created) and the decline path (no `Chore` is created, request is marked declined).

## 14. Simulated reminder states
Goal: Show upcoming/overdue reminder indicators without sending real email.
Description: Add derived (not stored) reminder state to the board from Task 7, computing "upcoming" or "overdue" per chore by comparing its due date/time against the current time at render time. Display this as a simulated email reminder badge or note per the spec, with tests covering a chore that's upcoming, one that's overdue, and one with no due date.

## 15. Weekly family summary view
Goal: Give the family a read-only rollup of the week's chore activity.
Description: Build a read-only view aggregating, per child, the count of chores completed (approved) and cash earned during the current calendar week, matching the spec's "Weekly family summary" screen. Add tests covering a week with no activity, one child's activity, and multiple children's activity in the same week.

## 16. Kid dashboard view
Goal: Give children a personalized view alongside the shared board.
Description: Build the Kid Mode dashboard from the spec's "Suggested Screens": the child's assigned chores, available chores to claim, current balance, their own pending chore requests, and their own completion/payout history. This view composes data already exposed by Tasks 4, 5, 7, 8, and 13 rather than introducing new business logic, and should have no leaderboard or cross-child ranking per the spec's explicit scope.
