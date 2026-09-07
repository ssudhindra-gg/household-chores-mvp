# Shared Household Chores: MVP Specification

## Goal

Create a desktop-first tool for a single family to coordinate household chores, give children a clear shared view of work, and track cash rewards fairly. The first version uses a simple local Parent/Kid mode switch rather than real accounts.

## Users

- Parents/guardians: create and manage chores, approve completed work, and record cash payouts.
- Children of mixed ages: see the whole household board, claim or complete chores, request chores, and track earnings.

## Core Chore Flow

1. A parent creates a chore with a title, optional notes, category, priority, due date/time, and optional cash reward.
2. The chore is either assigned to a child, available for a child to claim, or marked shared so multiple children may claim it.
3. A child marks the chore complete.
4. A parent approves it or rejects it with an explanation. A rejected chore returns to the child.
5. Approval adds the cash reward, when present, to that child’s unpaid balance.
6. A parent records a payout with amount and date, reducing the unpaid balance and preserving payout history.

## Chore Management

- Create both recurring chores and one-off chores.
- Recurring schedules support daily, weekly, and custom schedules.
- Automatically rotate recurring chores among children for fairness.
- Keep overdue chores available until completed or changed by a parent.
- Support unpaid chores as well as rewarded chores.
- Use categories such as kitchen, laundry, and outdoors.
- Offer normal and urgent priorities.
- Allow children to request chores from the family board.

## Views

### Parent Mode

- Shared household chore board with statuses: available, claimed, awaiting approval, approved, and returned.
- Create, edit, assign, rotate, and prioritize chores.
- Approve or reject submissions, including a rejection reason.
- See each child’s earned and unpaid cash balance.
- Record payouts and view payout history.
- Set an optional weekly earning limit for each child.
- View a weekly summary of chores completed and cash earned.

### Kid Mode

- See the entire household chore board and each child’s balance.
- Claim available chores and see assigned chores.
- Mark chores complete.
- Request additional chores.
- See their own completion and payout history.
- No leaderboard: the experience stays cooperative rather than competitive.

## Reminders

- Show simulated email reminders for upcoming and overdue chores.
- Actual email delivery is out of scope for the MVP.

## Explicitly Out of Scope

- Real logins, authentication, and separate household accounts.
- Real email, SMS, or push notifications.
- Photo proof for completed chores.
- Automated or electronic cash payments.
- A competitive leaderboard.

## Suggested Screens

1. Family chore board: filters by child, category, status, and priority.
2. Parent chore editor: create or modify chores and schedules.
3. Parent approvals and payouts: review completed chores, balances, and payout history.
4. Kid dashboard: assigned chores, available chores, balance, and requested chores.
5. Weekly family summary: completion counts and earnings by child.

## Acceptance Criteria

- A parent can create, assign, rotate, and schedule chores.
- A child can view the shared board, claim an available chore, and mark it complete.
- A parent can approve or return a completed chore with a reason.
- Approved rewarded chores increase the correct child’s unpaid balance.
- Parents can record payouts, and payout history remains visible.
- The app shows simulated email reminder states without sending messages.
- The board supports both rewarded and unpaid chores.
