import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chores.balances import unpaid_balance
from chores.forms import PayoutForm
from chores.models import Child, Chore, Payout, PayoutImmutable
from chores.payouts import (
    PayoutImmutableError,
    PayoutValidationError,
    payout_history,
    record_payout,
    validate_payout,
)


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

        payout = Payout.objects.record(
            self.ana, Decimal("4.00"), datetime.date(2026, 1, 5)
        )

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


class PayoutIssueFiveRuleTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")

    def earn(self, amount):
        chore = Chore.objects.create(title="Dishes", reward_amount=amount)
        chore.claim(self.ana)
        chore.complete()
        chore.approve()

    def assertCode(self, code, function, *args):
        with self.assertRaises(PayoutValidationError) as context:
            function(*args)
        self.assertEqual(context.exception.code, code)

    def test_amount_types_and_precision_have_stable_codes(self):
        for value in (None, "", "abc", Decimal("NaN"), Decimal("Infinity"), 5.0, True, Decimal("5.005"), Decimal("12345678.00")):
            with self.subTest(value=repr(value)):
                self.assertCode("invalid_amount", validate_payout, self.ana, value, datetime.date(2026, 1, 1))

        for value in (Decimal("0"), Decimal("0.00"), Decimal("-0.00"), Decimal("-1.00")):
            with self.subTest(value=repr(value)):
                self.assertCode("amount_not_positive", validate_payout, self.ana, value, datetime.date(2026, 1, 1))

    def test_dates_are_strict_and_cannot_be_in_the_future(self):
        for value in (timezone.now(), "2026-09-07", 0):
            with self.subTest(value=repr(value)):
                self.assertCode("invalid_date", validate_payout, self.ana, Decimal("1.00"), value)
        self.assertCode(
            "future_date",
            validate_payout,
            self.ana,
            Decimal("1.00"),
            timezone.localdate() + datetime.timedelta(days=1),
        )

    def test_default_date_uses_localdate_and_integer_and_string_amounts_normalise(self):
        self.earn(Decimal("20.00"))
        with patch("chores.payouts.timezone.localdate", return_value=datetime.date(2026, 9, 7)):
            first = record_payout(self.ana, 5)
            second = record_payout(self.ana, "5.00", datetime.date(2026, 9, 6))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.amount, Decimal("5.00"))
        self.assertEqual(first.paid_on, datetime.date(2026, 9, 7))
        self.assertEqual(second.amount, Decimal("5.00"))

    def test_balance_ceiling_is_exact_and_has_a_code_and_message(self):
        self.earn(Decimal("10.00"))
        payout = record_payout(self.ana, Decimal("10.00"), datetime.date(2026, 1, 1))
        self.assertEqual(payout.amount, Decimal("10.00"))
        self.assertEqual(str(unpaid_balance(self.ana)), "0.00")
        with self.assertRaises(PayoutValidationError) as context:
            record_payout(self.ana, Decimal("0.01"), datetime.date(2026, 1, 1))
        self.assertEqual(context.exception.code, "exceeds_balance")
        self.assertIn("0.01", str(context.exception))
        self.assertIn("0.00", str(context.exception))
        self.assertEqual(Payout.objects.count(), 1)

    def test_history_is_lazy_and_newest_first(self):
        first = Payout.objects.create(
            child=self.ana, amount=Decimal("1.00"), paid_on=datetime.date(2026, 1, 1)
        )
        second = Payout.objects.create(child=self.ana, amount=Decimal("2.00"), paid_on=datetime.date(2026, 1, 5))
        third = Payout.objects.create(child=self.ana, amount=Decimal("3.00"), paid_on=datetime.date(2026, 1, 5))
        with self.assertNumQueries(0):
            history = payout_history(self.ana)
        with self.assertNumQueries(1):
            self.assertEqual(list(history), [third, second, first])

    def test_post_insert_guard_rolls_back_when_precheck_is_bypassed(self):
        self.earn(Decimal("5.00"))
        with patch("chores.payouts.validate_payout", return_value=Decimal("6.00")):
            with self.assertRaises(PayoutValidationError) as context:
                record_payout(self.ana, Decimal("6.00"), datetime.date(2026, 1, 1))
        self.assertEqual(context.exception.code, "exceeds_balance")
        self.assertEqual(Payout.objects.count(), 0)

    def test_queryset_update_and_delete_are_append_only(self):
        payout = Payout.objects.create(
            child=self.ana, amount=Decimal("1.00"), paid_on=datetime.date(2026, 1, 1)
        )
        for operation in (
            lambda: Payout.objects.all().delete(),
            lambda: Payout.objects.filter(pk=payout.pk).update(amount=Decimal("2.00")),
            lambda: self.ana.payouts.all().delete(),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(PayoutImmutable):
                    operation()
        self.assertEqual(Payout.objects.get(pk=payout.pk).amount, Decimal("1.00"))


class PayoutFormAndAdminTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        chore = Chore.objects.create(title="Dishes", reward_amount=Decimal("10.00"))
        chore.claim(self.ana)
        chore.complete()
        chore.approve()
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="parent", email="parent@example.com", password="password"
        )
        self.client.force_login(self.superuser)

    def test_form_has_exact_fields_and_delegates_validation(self):
        self.assertEqual(list(PayoutForm.base_fields), ["child", "amount", "paid_on"])
        form = PayoutForm(
            data={"child": self.ana.pk, "amount": "11.00", "paid_on": "2026-01-01"}
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors.as_data()["amount"][0].code, "exceeds_balance")

    def test_form_save_uses_the_single_writer(self):
        form = PayoutForm(
            data={"child": self.ana.pk, "amount": "3.00", "paid_on": "2026-01-01"}
        )
        self.assertTrue(form.is_valid())
        payout = form.save()
        self.assertEqual(payout.amount, Decimal("3.00"))
        self.assertEqual(unpaid_balance(self.ana), Decimal("7.00"))

    def test_admin_add_change_and_delete_behave_as_append_only(self):
        add_url = reverse("admin:chores_payout_add")
        response = self.client.get(add_url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            add_url,
            {"child": self.ana.pk, "amount": "2.00", "paid_on": "2026-01-01", "_save": "Save"},
        )
        self.assertEqual(response.status_code, 302)
        payout = Payout.objects.get()
        self.assertEqual(unpaid_balance(self.ana), Decimal("8.00"))
        change = self.client.get(reverse("admin:chores_payout_change", args=[payout.pk]))
        self.assertEqual(change.status_code, 200)
        self.assertNotContains(change, 'name="_save"')
        delete = self.client.get(reverse("admin:chores_payout_delete", args=[payout.pk]))
        self.assertEqual(delete.status_code, 403)
