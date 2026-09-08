# Shared Household Chores: Architecture & Tech Stack

This document records the tech stack decision and system design for the MVP described in
[`household-chores-mvp-spec.md`](./household-chores-mvp-spec.md).

## Deployment Target

A **browser-based web app**, optimized for desktop screen sizes first ("desktop-first"), rather
than an installed native application. Any family device on the home network reaches it via a
URL, with no per-device install step and no sync problem across devices — everyone sees the same
shared server state.

## Tech Stack Decision

**Django + Django ORM + SQLite**, with `django-htmx` for partial-page interactivity (claim /
complete / approve without full page reloads), and a customized **Django Admin** covering most of
the parent-side CRUD screens.

### Why Django

The domain has real relational structure — chores, children, statuses, payouts, balances — and a
state machine governing chore status transitions. Django's ORM, migrations, and forms map onto
this cleanly with less hand-written boilerplate than a minimal framework would need.

The concrete deciding factor was the **Parent Approvals & Payouts** screen. Django Admin
(customized) can cover most of it almost for free:

- Admin actions for "Approve selected" / "Reject selected" (with a reason field)
- Built-in sorting, filtering, search, and pagination on the chore list
- `ModelForm`-based validation for payout recording, with far less hand-written form/validation
  code than a minimal framework would need

The trade-off is that parent-facing admin screens look more like an admin panel unless time is
spent skinning them, versus a fully custom UI built by hand in a minimal framework. That cost is
acceptable here since Admin is parent-only — kids never see it.

### Options Considered

| Option | Stack | Verdict |
|---|---|---|
| 1 | FastAPI + HTMX + SQLite | Strong alternative — less framework overhead, more custom control over every screen, but no equivalent to Django Admin, so the approvals/payouts screen requires more hand-written code |
| 2 | Next.js + Prisma + SQLite | Most "app-like" feel via a React SPA, but adds a second stack/language and more tooling than this MVP needs |
| 3 | Minimal Flask + vanilla templates + SQLite | Fastest to stand up, but hand-rolls validation/migrations that Django or FastAPI provide |
| 4 | Tauri + React/Svelte + SQLite (native desktop) | Only relevant if "desktop-first" meant an installed app; rejected because a per-device install has no natural multi-device sync story for a shared family board |
| **5 (chosen)** | **Django + Django ORM + SQLite** | Best fit: relational domain, state machine, and admin-heavy parent screens all play to Django's strengths |

Kid Mode (board view, claim, complete, request, own history) is roughly equal effort under any of
the server-rendered options (1, 3, 5) — Django Admin's advantage is specific to the parent-side
screens.

## Architecture Overview

A single Django project with:

- A `chores` app containing the core models (`Chore`, `Child`, `ChoreClaim`, `Payout`,
  `RecurrenceRule`, `ChoreRequest`) plus the board and kid-facing views.
- A thin **Parent/Kid mode switch** implemented as a session flag toggled by a UI control — no
  real accounts or authentication, matching the spec's explicit scope.
- SQLite as the database file. Runs via Django's dev server, or `gunicorn` for steadier
  always-on availability on the home LAN, so any family device can reach it by browser.

## Data Model (Conceptual)

No code yet — this describes the entities and relationships, not implementation.

- **`Child`** — name, optional weekly earning limit, computed unpaid balance (derived from
  approved rewarded chores minus recorded payouts).
- **`Chore`** — title, optional notes, category, priority (normal/urgent), due date/time, optional
  reward amount (absent = unpaid chore), status (`available` / `claimed` / `awaiting approval` /
  `approved` / `returned`), assigned child (nullable — null means available/shared), shared flag
  (multiple children may claim), link to a `RecurrenceRule` when recurring. The assigned-child
  field remains the earliest-claim compatibility pointer.
- **`ChoreClaim`** — one ordered claim per child and chore, with claim/completion timestamps and
  the child's cent-exact reward share after approval. Shared chores keep all claim rows until a
  child gives up the claim; balances sum approved claim shares and fall back to legacy chore
  rewards for chores without claim rows.
- **`RecurrenceRule`** — daily / weekly / custom schedule, plus the rotation order among children
  for fairness.
- **`Payout`** — child, amount, date. Recording a payout reduces the child's unpaid balance while
  preserving payout history (payouts are never deleted, only added).
- **`ChoreRequest`** — a kid-submitted request for a new chore, awaiting a parent turning it into
  a real `Chore` (or declining it).

Reminder state is **derived, not stored**: computed at render time from due dates against the
current time, to produce the simulated "upcoming" / "overdue" email reminder states described in
the spec. No real email is sent.

## Screens → Implementation

| Screen | Approach |
|---|---|
| Family chore board & Kid dashboard | Custom views/templates. One shared board template reused by both modes, with action buttons swapped based on the current mode. `django-htmx` partials handle claim/complete without a full reload. |
| Parent chore editor (create/edit/rotate/schedule) | Custom `ModelForm`-based views. Recurrence and rotation configuration is bespoke UI, not a good fit for the generic admin interface. |
| Parent approvals & payouts | Primarily **customized Django Admin**: admin actions for approve/reject (with a required reason field on reject), inline payout recording, and the built-in list filtering/search/pagination. |
| Weekly family summary | A read-only custom view aggregating each child's completions and earnings over the current week. |

## State Machine & Data Flow

Chore status transitions —

```
available → claimed → awaiting approval → approved
                                     └───→ returned → (back to available/claimed)
```

— are enforced in model methods or a small service layer, **not** scattered across views, so
every entry point (board actions, admin actions) goes through the same rules. Approval is the
single place where a rewarded chore's cash amount is allocated to claimant shares, keeping the
money math auditable from one code path. A shared chore may accept another claim while already
claimed; completion and release name a claimant when more than one child is present.

## Error Handling & Validation

- Django `Form`/`ModelForm` validation on chore creation and editing: due dates, reward amounts,
  and recurrence/rotation configuration.
- Payout recording validation: amount must be greater than zero and must not exceed the child's
  current unpaid balance.
- Rejecting a chore requires a non-empty reason.
- **Assumption to confirm:** weekly earning limits are enforced as a **soft warning** shown at
  approval time, not a hard block — the spec describes tracking the limit but doesn't say
  approvals must be prevented once it's reached.

## Testing Approach

Django's built-in test framework (`TestCase` + test client):

- **Model-level tests** for the chore status state machine, balance math on approval/payout, and
  recurring-chore rotation fairness.
- **View-level tests** for the actions permitted in each mode (e.g. only Parent Mode can approve/
  reject or record payouts; only Kid Mode can claim/request).
