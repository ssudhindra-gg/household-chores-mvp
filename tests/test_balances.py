"""Tests for the derived unpaid balance (issue #4).

Scope: the money maths in :mod:`chores.balances` -- what counts toward a
balance, the shape of the number that comes back, the fact that #3's
``approve()`` is the only thing that moves it, and the query counts that keep
a whole family's balances to one round trip.

Wherever a test is *about* approval the chore is driven through
``claim() -> complete() -> approve()``; ``status`` is never assigned directly.
Tests that only need a row sitting in some state build it directly and say so.
``Payout`` rows are created with ``Payout.objects.create(...)`` because #5's
recording flow and its validation do not exist yet.
"""

import ast
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.db.models import DecimalField
from django.test import SimpleTestCase, TestCase

from chores import balances
from chores.balances import (
    MONEY,
    annotate_balances,
    total_earned,
    total_paid_out,
    unpaid_balance,
)
from chores.models import Child, Chore, Payout
from chores.state_machine import InvalidChoreTransition

APP_DIR = Path(balances.__file__).resolve().parent
BALANCES_SOURCE = (APP_DIR / "balances.py").read_text(encoding="utf-8")

ZERO = Decimal("0.00")

# Every status a chore can sit in that must contribute nothing.
UNAPPROVED_STATUSES = [
    Chore.Status.AVAILABLE,
    Chore.Status.CLAIMED,
    Chore.Status.AWAITING_APPROVAL,
    Chore.Status.RETURNED,
]

# Method names that would write to the database.
WRITE_METHODS = {
    "save",
    "create",
    "update",
    "delete",
    "get_or_create",
    "update_or_create",
    "bulk_create",
    "bulk_update",
    "add",
    "remove",
    "set",
}


def approved_chore(child, amount, title="Chore"):
    """A chore taken all the way to ``approved`` through #3's own methods."""
    chore = Chore.objects.create(title=title, reward_amount=amount)
    chore.claim(child)
    chore.complete()
    chore.approve()
    return chore


class ModuleShapeTests(SimpleTestCase):
    """Where the code lives, and what it is not allowed to contain."""

    def test_module_exposes_the_four_callables(self):
        for name in ("total_earned", "total_paid_out", "unpaid_balance",
                     "annotate_balances"):
            self.assertTrue(callable(getattr(balances, name)), name)

    def test_child_gains_no_balance_property_or_field(self):
        # A same-named read-only property is a data descriptor, so Django's
        # setattr of the annotation would fail -- and it would reintroduce
        # the per-child query the annotation exists to avoid.
        for name in ("unpaid_balance", "total_earned", "total_paid_out",
                     "balance"):
            self.assertFalse(hasattr(Child, name), f"Child.{name} exists")
            self.assertNotIn(
                name, {field.name for field in Child._meta.get_fields()}
            )

    def test_state_machine_still_holds_no_money_arithmetic(self):
        # The hook into approval is that approve() is the only writer of
        # status="approved" -- not a credit step inside it.
        source = (APP_DIR / "state_machine.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(
                    node.attr, {"reward_amount", "amount", "balance"}
                )

    def test_balances_module_performs_no_writes(self):
        tree = ast.parse(BALANCES_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(
                    node.func.attr,
                    WRITE_METHODS,
                    f"{node.func.attr}() is a write, on line {node.lineno}",
                )

    def test_balances_module_contains_no_float_anywhere(self):
        tree = ast.parse(BALANCES_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                self.assertNotIsInstance(
                    node.value, float, f"float literal on line {node.lineno}"
                )
            if isinstance(node, ast.Name):
                self.assertNotEqual(
                    node.id, "float", f"float() on line {node.lineno}"
                )

    def test_annotate_balances_never_reaches_for_child_objects(self):
        # It annotates the queryset it is handed, so callers keep their own
        # filtering and ordering.
        tree = ast.parse(BALANCES_SOURCE)
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "annotate_balances"
        )
        names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
        self.assertNotIn("Child", names)

    def test_sums_are_declared_wider_than_the_amounts_they_add_up(self):
        self.assertIsInstance(MONEY, DecimalField)
        self.assertEqual(MONEY.max_digits, 12)
        self.assertEqual(MONEY.decimal_places, 2)
        # Deliberately wider than an individual amount from #2.
        self.assertEqual(Chore._meta.get_field("reward_amount").max_digits, 7)


class MigrationStateTests(TestCase):
    """The balance is derived, so nothing here adds a field or a migration."""

    def test_no_model_change_needs_a_migration(self):
        # Raises SystemExit if the models and the migrations disagree.
        call_command(
            "makemigrations",
            "--check",
            "--dry-run",
            stdout=StringIO(),
            stderr=StringIO(),
        )


class WhatCountsTests(TestCase):
    """Which rows land in a balance and which do not."""

    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.bo = Child.objects.create(name="Bo")

    def test_only_approved_chores_count(self):
        for status in UNAPPROVED_STATUSES:
            with self.subTest(status=status):
                # Built directly: the test is about a row *sitting* in this
                # state, not about how it got there.
                chore = Chore.objects.create(
                    title=f"A {status} chore",
                    status=status,
                    assigned_child=self.ana,
                    reward_amount=Decimal("5.00"),
                )
                self.assertEqual(total_earned(self.ana), ZERO)
                self.assertEqual(unpaid_balance(self.ana), ZERO)
                chore.delete()

    def test_a_reward_counts_for_its_assigned_child_only(self):
        approved_chore(self.ana, Decimal("5.00"))

        self.assertEqual(total_earned(self.ana), Decimal("5.00"))
        self.assertEqual(total_earned(self.bo), ZERO)
        self.assertEqual(unpaid_balance(self.bo), ZERO)

    def test_an_unpaid_chore_contributes_nothing_and_does_not_null_the_total(self):
        approved_chore(self.ana, None)

        self.assertEqual(total_earned(self.ana), ZERO)
        self.assertIsNotNone(unpaid_balance(self.ana))
        self.assertEqual(unpaid_balance(self.ana), ZERO)

    def test_a_zero_reward_is_legal_and_contributes_nothing(self):
        approved_chore(self.ana, Decimal("0.00"))

        self.assertEqual(total_earned(self.ana), ZERO)
        self.assertEqual(str(total_earned(self.ana)), "0.00")

    def test_an_approved_chore_with_no_assigned_child_lands_nowhere(self):
        # Only reachable by hand-editing in the plain admin from #2: #3's
        # approve() refuses a chore with no assigned child. Built directly
        # for exactly that reason.
        Chore.objects.create(
            title="Orphaned",
            status=Chore.Status.APPROVED,
            assigned_child=None,
            reward_amount=Decimal("42.00"),
        )

        for child in (self.ana, self.bo):
            with self.subTest(child=child.name):
                self.assertEqual(total_earned(child), ZERO)
                self.assertEqual(unpaid_balance(child), ZERO)

        rows = {row.name: row for row in annotate_balances(Child.objects.all())}
        self.assertEqual(rows["Ana"].unpaid_balance, ZERO)
        self.assertEqual(rows["Bo"].unpaid_balance, ZERO)

    def test_every_payout_subtracts_whatever_its_date(self):
        approved_chore(self.ana, Decimal("20.00"))
        Payout.objects.create(
            child=self.ana, amount=Decimal("5.00"), paid_on="2020-01-01"
        )
        Payout.objects.create(
            child=self.ana, amount=Decimal("2.50"), paid_on="2030-12-31"
        )

        self.assertEqual(total_paid_out(self.ana), Decimal("7.50"))
        self.assertEqual(unpaid_balance(self.ana), Decimal("12.50"))

    def test_another_childs_payouts_never_subtract(self):
        approved_chore(self.ana, Decimal("20.00"))
        Payout.objects.create(
            child=self.bo, amount=Decimal("5.00"), paid_on="2026-01-01"
        )

        self.assertEqual(total_paid_out(self.ana), ZERO)
        self.assertEqual(unpaid_balance(self.ana), Decimal("20.00"))
        self.assertEqual(unpaid_balance(self.bo), Decimal("-5.00"))

    def test_the_balance_goes_negative_rather_than_being_clamped(self):
        # An over-payout is a data error worth seeing; #5 is what stops one
        # being recorded in the first place.
        approved_chore(self.ana, Decimal("4.00"))
        Payout.objects.create(
            child=self.ana, amount=Decimal("10.00"), paid_on="2026-01-01"
        )

        balance = unpaid_balance(self.ana)
        self.assertEqual(balance, Decimal("-6.00"))
        self.assertIsInstance(balance, Decimal)
        self.assertLess(balance, ZERO)


class TheNumberItselfTests(TestCase):
    """The type, the zero, the width and the rounding of what comes back."""

    def setUp(self):
        self.ana = Child.objects.create(name="Ana")

    def test_every_return_value_is_a_decimal(self):
        approved_chore(self.ana, Decimal("3.00"))
        Payout.objects.create(
            child=self.ana, amount=Decimal("1.00"), paid_on="2026-01-01"
        )

        for value in (
            total_earned(self.ana),
            total_paid_out(self.ana),
            unpaid_balance(self.ana),
        ):
            self.assertIsInstance(value, Decimal)

    def test_a_child_with_no_activity_gets_two_place_zero_not_none(self):
        for value in (
            total_earned(self.ana),
            total_paid_out(self.ana),
            unpaid_balance(self.ana),
        ):
            self.assertIsNotNone(value)
            self.assertIsInstance(value, Decimal)
            self.assertEqual(value, ZERO)
            self.assertEqual(str(value), "0.00")

    def test_a_sum_may_be_wider_than_a_single_amount(self):
        # Each reward is the widest max_digits=7 allows; their sum is not.
        approved_chore(self.ana, Decimal("99999.99"), title="One")
        approved_chore(self.ana, Decimal("99999.99"), title="Two")

        self.assertEqual(total_earned(self.ana), Decimal("199999.98"))
        self.assertEqual(unpaid_balance(self.ana), Decimal("199999.98"))

    def test_three_ten_pence_rewards_come_back_as_exactly_thirty(self):
        # SQLite sums DecimalField values through a float, so this is the
        # test that the result is quantised rather than 0.30000000000000004.
        for index in range(3):
            approved_chore(self.ana, Decimal("0.10"), title=f"Dime {index}")

        earned = total_earned(self.ana)
        self.assertEqual(earned, Decimal("0.30"))
        self.assertEqual(str(earned), "0.30")
        self.assertEqual(str(unpaid_balance(self.ana)), "0.30")

    def test_a_hundred_penny_rewards_come_back_as_exactly_one_pound(self):
        for index in range(100):
            approved_chore(self.ana, Decimal("0.01"), title=f"Penny {index}")

        earned = total_earned(self.ana)
        self.assertEqual(earned, Decimal("1.00"))
        self.assertEqual(str(earned), "1.00")

    def test_payout_sums_are_quantised_too(self):
        for _ in range(3):
            Payout.objects.create(
                child=self.ana, amount=Decimal("0.10"), paid_on="2026-01-01"
            )

        self.assertEqual(str(total_paid_out(self.ana)), "0.30")


class ApprovalHookTests(TestCase):
    """#3's ``approve()`` is the only thing that can move a balance."""

    def setUp(self):
        self.ana = Child.objects.create(name="Ana")

    def test_approving_raises_the_balance_by_exactly_the_reward(self):
        chore = Chore.objects.create(title="Dishes", reward_amount=Decimal("5.00"))

        chore.claim(self.ana)
        self.assertEqual(unpaid_balance(self.ana), ZERO)

        chore.complete()
        self.assertEqual(unpaid_balance(self.ana), ZERO)

        before = unpaid_balance(self.ana)
        chore.approve()
        self.assertEqual(unpaid_balance(self.ana) - before, chore.reward_amount)

    def test_approvals_accumulate(self):
        approved_chore(self.ana, Decimal("5.00"), title="Dishes")
        self.assertEqual(unpaid_balance(self.ana), Decimal("5.00"))

        approved_chore(self.ana, Decimal("2.50"), title="Bins")
        self.assertEqual(unpaid_balance(self.ana), Decimal("7.50"))

    def test_approving_twice_raises_and_credits_nothing_further(self):
        chore = approved_chore(self.ana, Decimal("5.00"))
        self.assertEqual(unpaid_balance(self.ana), Decimal("5.00"))

        with self.assertRaises(InvalidChoreTransition):
            chore.approve()

        self.assertEqual(unpaid_balance(self.ana), Decimal("5.00"))

    def test_a_rejected_then_redone_chore_credits_its_reward_once(self):
        chore = Chore.objects.create(title="Dishes", reward_amount=Decimal("5.00"))
        chore.claim(self.ana)
        chore.complete()
        chore.reject("Missed the pans")
        self.assertEqual(unpaid_balance(self.ana), ZERO)

        chore.claim(self.ana)
        chore.complete()
        chore.approve()

        self.assertEqual(unpaid_balance(self.ana), Decimal("5.00"))
        self.assertEqual(total_earned(self.ana), Decimal("5.00"))


class AnnotationTests(TestCase):
    """The whole family in one query, agreeing with the single-child answers."""

    def setUp(self):
        # Three children: one with chores and payouts, one with chores only,
        # one with no activity at all.
        self.ana = Child.objects.create(name="Ana")
        self.bo = Child.objects.create(name="Bo")
        self.cy = Child.objects.create(name="Cy")

        approved_chore(self.ana, Decimal("5.00"), title="Dishes")
        approved_chore(self.ana, Decimal("2.50"), title="Bins")
        Payout.objects.create(
            child=self.ana, amount=Decimal("1.00"), paid_on="2026-01-01"
        )
        Payout.objects.create(
            child=self.ana, amount=Decimal("0.50"), paid_on="2026-02-01"
        )
        approved_chore(self.bo, Decimal("3.00"), title="Laundry")

    def test_annotation_agrees_with_the_single_child_functions(self):
        # Ana has two approved chores *and* two payouts: a naive pair of
        # Sum()s over the joined relations would multiply the rows of each by
        # the count of the other and inflate both totals.
        for row in annotate_balances(Child.objects.all()):
            with self.subTest(child=row.name):
                child = Child.objects.get(pk=row.pk)
                self.assertEqual(row.total_earned, total_earned(child))
                self.assertEqual(row.total_paid_out, total_paid_out(child))
                self.assertEqual(row.unpaid_balance, unpaid_balance(child))

    def test_annotated_totals_are_not_inflated_by_a_join(self):
        row = annotate_balances(Child.objects.filter(pk=self.ana.pk)).get()

        self.assertEqual(row.total_earned, Decimal("7.50"))
        self.assertEqual(row.total_paid_out, Decimal("1.50"))
        self.assertEqual(row.unpaid_balance, Decimal("6.00"))

    def test_a_child_with_no_activity_annotates_to_zero_not_none(self):
        row = annotate_balances(Child.objects.filter(pk=self.cy.pk)).get()

        for value in (row.total_earned, row.total_paid_out, row.unpaid_balance):
            self.assertIsNotNone(value)
            self.assertIsInstance(value, Decimal)
            self.assertEqual(value, ZERO)

    def test_unpaid_balance_is_earned_minus_paid_out_for_every_child(self):
        for child in Child.objects.all():
            with self.subTest(child=child.name):
                self.assertEqual(
                    unpaid_balance(child),
                    total_earned(child) - total_paid_out(child),
                )

    def test_the_callers_own_filtering_and_ordering_survive(self):
        queryset = annotate_balances(
            Child.objects.exclude(pk=self.cy.pk).order_by("-name")
        )

        self.assertEqual([row.name for row in queryset], ["Bo", "Ana"])


class QueryCountTests(TestCase):
    """No N+1: a balance costs one query, a family costs one query."""

    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        Child.objects.create(name="Bo")
        Child.objects.create(name="Cy")
        approved_chore(self.ana, Decimal("5.00"))
        Payout.objects.create(
            child=self.ana, amount=Decimal("1.00"), paid_on="2026-01-01"
        )

    def test_each_single_child_function_costs_one_query(self):
        for function in (total_earned, total_paid_out, unpaid_balance):
            with self.subTest(function=function.__name__):
                with self.assertNumQueries(1):
                    function(self.ana)

    def test_the_whole_family_costs_exactly_one_query(self):
        with self.assertNumQueries(1):
            rows = list(annotate_balances(Child.objects.all()))
            # Reading the annotations must not go back to the database.
            [(row.total_earned, row.total_paid_out, row.unpaid_balance)
             for row in rows]

        self.assertEqual(len(rows), 3)
