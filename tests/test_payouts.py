import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from chores.balances import unpaid_balance
from chores.models import Child, Chore, Payout
from chores.payouts import PayoutImmutableError, PayoutValidationError, record_payout


class PayoutRecordingTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")

    def approve_reward(self, amount, title="Dishes"):
        chore = Chore.objects.create(title=title, reward_amount=amount)
        chore.claim(self.ana)
        chore.complete()
        chore.approve()

    def test_recording_a_covered_payout_reduces_the_derived_balance(self):
        self.approve_reward(Decimal("10.00"))

        payout = record_payout(self.ana, Decimal("3.25"), datetime.date(2026, 1, 5))

        self.assertEqual(payout.child_id, self.ana.pk)
        self.assertEqual(payout.amount, Decimal("3.25"))
        self.assertEqual(payout.paid_on, datetime.date(2026, 1, 5))
        self.assertEqual(unpaid_balance(self.ana), Decimal("6.75"))

    def test_manager_record_is_the_same_validated_operation(self):
        self.approve_reward(Decimal("4.00"))

        payout = Payout.objects.record(self.ana, Decimal("4.00"), "2026-01-05")

        self.assertEqual(payout.amount, Decimal("4.00"))
        self.assertEqual(unpaid_balance(self.ana), Decimal("0.00"))

    def test_non_positive_or_invalid_amount_is_rejected_without_a_row(self):
        self.approve_reward(Decimal("10.00"))

        for amount in (Decimal("0.00"), Decimal("-0.01"), "not-money"):
            with self.subTest(amount=amount), self.assertRaises(PayoutValidationError):
                record_payout(self.ana, amount, datetime.date(2026, 1, 5))

        self.assertEqual(Payout.objects.count(), 0)
        self.assertEqual(unpaid_balance(self.ana), Decimal("10.00"))

    def test_amount_above_balance_is_rejected_without_a_row(self):
        self.approve_reward(Decimal("5.00"))

        with self.assertRaises(PayoutValidationError) as context:
            record_payout(self.ana, Decimal("5.01"), datetime.date(2026, 1, 5))

        self.assertIn("unpaid balance", str(context.exception))
        self.assertEqual(Payout.objects.count(), 0)
        self.assertEqual(unpaid_balance(self.ana), Decimal("5.00"))

    def test_unpaid_chores_do_not_make_money_available_for_payout(self):
        chore = Chore.objects.create(title="Read", reward_amount=None)
        chore.claim(self.ana)
        chore.complete()
        chore.approve()

        with self.assertRaises(PayoutValidationError):
            record_payout(self.ana, Decimal("0.01"), datetime.date(2026, 1, 5))

        self.assertEqual(unpaid_balance(self.ana), Decimal("0.00"))

    def test_multiple_payouts_remain_queryable_in_history_order(self):
        self.approve_reward(Decimal("10.00"))
        first = record_payout(self.ana, Decimal("2.00"), datetime.date(2026, 1, 1))
        second = record_payout(self.ana, Decimal("3.00"), datetime.date(2026, 1, 5))
        same_day = record_payout(self.ana, Decimal("1.00"), datetime.date(2026, 1, 5))

        self.assertEqual(list(self.ana.payouts.all()), [same_day, second, first])
        self.assertEqual(unpaid_balance(self.ana), Decimal("4.00"))

    def test_existing_payouts_are_immutable(self):
        self.approve_reward(Decimal("10.00"))
        payout = record_payout(self.ana, Decimal("2.00"), datetime.date(2026, 1, 5))

        payout.amount = Decimal("1.00")
        with self.assertRaises(PayoutImmutableError):
            payout.save()
        with self.assertRaises(PayoutImmutableError):
            payout.delete()
        with self.assertRaises(PayoutImmutableError):
            Payout.objects.filter(pk=payout.pk).delete()

        fresh = Payout.objects.get(pk=payout.pk)
        self.assertEqual(fresh.amount, Decimal("2.00"))
        self.assertEqual(Payout.objects.count(), 1)

    def test_validation_error_does_not_change_existing_history(self):
        self.approve_reward(Decimal("10.00"))
        existing = record_payout(self.ana, Decimal("2.00"), datetime.date(2026, 1, 5))

        with self.assertRaises(PayoutValidationError):
            record_payout(self.ana, Decimal("8.01"), datetime.date(2026, 1, 6))

        self.assertEqual(list(self.ana.payouts.all()), [existing])
        self.assertEqual(unpaid_balance(self.ana), Decimal("8.00"))


class PayoutInputTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")

    def test_saved_child_is_required(self):
        with self.assertRaises(PayoutValidationError):
            record_payout(Child(name="Unsaved"), Decimal("1.00"), datetime.date.today())

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(PayoutValidationError):
            record_payout(self.ana, Decimal("1.00"), "not-a-date")

    def test_existing_model_is_append_only_even_when_saved_directly(self):
        payout = Payout.objects.create(
            child=self.ana, amount=Decimal("1.00"), paid_on=datetime.date(2026, 1, 5)
        )
        with self.assertRaises((PayoutImmutableError, ValidationError)):
            payout.save()
