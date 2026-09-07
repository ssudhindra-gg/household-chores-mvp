## Goal

Parent Mode provides ModelForm-based create and edit screens for one-off chores
with the fields in the GitHub issue, while Kid Mode receives the normal mode
switch warning and cannot mutate chores through these routes.

## Acceptance criteria

- [ ] Parent Mode can open a create screen and submit a valid one-off chore
      with title, notes, category, priority, optional due date/time, optional
      reward, assignment, and shared flag.
- [ ] A valid create stores `recurrence_rule=None` and redirects to the board.
- [ ] Parent Mode can open an existing one-off chore in an edit screen and
      update the same fields without creating a second chore.
- [ ] Due date/time is optional; a supplied due date must be a valid aware
      datetime that is not in the past, and invalid submissions show a field
      error without writing a row.
- [ ] Reward is optional; non-negative values with at most two decimal places
      are accepted, while negative, over-precision, and over-sized values show
      a field error without writing or changing a chore.
- [ ] Edit and create forms preserve the existing category, priority, child,
      and shared choices from the domain model.
- [ ] Kid Mode cannot access either mutating view: it redirects with the
      existing Parent Mode warning and leaves the database unchanged.
- [ ] GET renders contain no HTMX action requirement; the editor is a normal
      form and uses the shared base mode bar.

## Out of scope

- Recurrence rules and rotation — Issue 10.
- Approval/rejection and Admin workflows — Issue 11.
- Authentication or real parent accounts — the MVP explicitly excludes them.

## Constraints

- Use a `ModelForm` in `chores/forms.py`, the existing `parent_mode_required`
      decorator, and namespaced routes in `chores/urls.py`.
- Use existing model/state-machine invariants; no direct status writes and no
      schema or dependency changes.
- Add focused view/form tests and commit the completed task before GitHub QA.
