## Goal

Parents can append a dated cash payout for a child, and the child's derived
unpaid balance decreases by exactly that amount.

## Acceptance criteria

- [ ] `record_payout(child, amount, paid_on)` creates one `Payout` row with the
      supplied child, positive two-place amount, and date.
- [ ] Recording an amount greater than the child's current unpaid balance is
      rejected with a clear validation error and creates no row.
- [ ] Recording zero, a negative amount, or a value that is not a valid money
      amount is rejected with a clear validation error and creates no row.
- [ ] A successful payout reduces `unpaid_balance(child)` by the recorded
      amount; approved unpaid chores do not increase the amount available to
      pay out.
- [ ] `child.payouts.all()` exposes the complete payout history, newest paid
      date first, including multiple payouts on the same date.
- [ ] An existing payout cannot be edited or deleted through the model API;
      payout history is append-only.
- [ ] A failed recording attempt leaves both the balance and payout count
      unchanged.

## Out of scope

- Parent/Kid mode checks and a browser form — Task 6.
- Customized Admin actions, list controls, and the parent approvals/payouts
  screen — Task 11.
- Weekly earning-limit warnings — Task 12.

## Constraints

- Keep the domain logic in `chores/payouts.py` and the existing `Payout`
  model; use the derived balance functions in `chores/balances.py`.
- Use Django ORM transactions and no new dependencies.
- Add focused tests and keep the full existing suite passing.
