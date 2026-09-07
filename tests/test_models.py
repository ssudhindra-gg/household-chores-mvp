"""Tests for the core domain models and their plain admin registration.

Scope matches issue #2: *shape only* -- fields, defaults, choice sets, the
database constraints that keep obviously bad rows out, deletion behaviour and
``__str__``. Behaviour (status transitions, balance maths, rotation, payout
validation) belongs to later issues and is deliberately untested here.

Constraint violations are wrapped in ``transaction.atomic()`` so the enclosing
``TestCase`` transaction survives the failed statement and later assertions in
the same test still run.
"""

import datetime
from decimal import Decimal

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chores.models import (
    Child,
    Chore,
    ChoreRequest,
    Payout,
    RecurrenceRule,
    RotationSlot,
)


class ChildModelTests(TestCase):
    def test_str_is_the_child_name(self):
        self.assertEqual(str(Child.objects.create(name="Ana")), "Ana")

    def test_name_is_unique(self):
        Child.objects.create(name="Ana")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Child.objects.create(name="Ana")

    def test_weekly_earning_limit_is_optional_and_defaults_to_none(self):
        self.assertIsNone(Child.objects.create(name="Ana").weekly_earning_limit)

    def test_weekly_earning_limit_is_a_two_place_decimal(self):
        field = Child._meta.get_field("weekly_earning_limit")
        self.assertEqual(field.get_internal_type(), "DecimalField")
        self.assertEqual((field.max_digits, field.decimal_places), (7, 2))
        Child.objects.create(name="Ana", weekly_earning_limit=Decimal("10.50"))
        limit = Child.objects.get(name="Ana").weekly_earning_limit
        self.assertIsInstance(limit, Decimal)
        self.assertEqual(limit, Decimal("10.50"))

    def test_negative_weekly_earning_limit_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Child.objects.create(name="Ana", weekly_earning_limit=Decimal("-1.00"))

    def test_zero_weekly_earning_limit_is_allowed(self):
        child = Child.objects.create(name="Ana", weekly_earning_limit=Decimal("0.00"))
        self.assertEqual(
            Child.objects.get(pk=child.pk).weekly_earning_limit, Decimal("0.00")
        )

    def test_default_ordering_is_by_name(self):
        Child.objects.create(name="Cara")
        Child.objects.create(name="Ana")
        Child.objects.create(name="Ben")
        self.assertEqual([c.name for c in Child.objects.all()], ["Ana", "Ben", "Cara"])

    def test_child_has_no_relation_to_the_auth_user_model(self):
        user_model = get_user_model()
        related = [
            field.name
            for field in Child._meta.get_fields()
            if getattr(field, "related_model", None) is user_model
        ]
        self.assertEqual(related, [])


class RecurrenceRuleModelTests(TestCase):
    def test_str_describes_the_schedule(self):
        rule = RecurrenceRule.objects.create(frequency=RecurrenceRule.Frequency.WEEKLY)
        self.assertEqual(str(rule), "weekly (every 1)")

    def test_frequency_choices_are_exactly_daily_weekly_custom(self):
        self.assertEqual(
            [value for value, _ in RecurrenceRule.Frequency.choices],
            ["daily", "weekly", "custom"],
        )

    def test_interval_defaults_to_one(self):
        rule = RecurrenceRule.objects.create(frequency=RecurrenceRule.Frequency.DAILY)
        self.assertEqual(RecurrenceRule.objects.get(pk=rule.pk).interval, 1)

    def test_custom_schedule_field_is_blank_and_empty_by_default(self):
        rule = RecurrenceRule.objects.create(frequency=RecurrenceRule.Frequency.CUSTOM)
        self.assertEqual(RecurrenceRule.objects.get(pk=rule.pk).weekdays, "")
        self.assertTrue(RecurrenceRule._meta.get_field("weekdays").blank)

    def test_next_rotation_position_defaults_to_zero(self):
        rule = RecurrenceRule.objects.create(frequency=RecurrenceRule.Frequency.WEEKLY)
        self.assertEqual(
            RecurrenceRule.objects.get(pk=rule.pk).next_rotation_position, 0
        )

    def test_a_rule_with_an_empty_rotation_order_is_legal(self):
        rule = RecurrenceRule.objects.create(frequency=RecurrenceRule.Frequency.WEEKLY)
        self.assertEqual(list(rule.rotation_slots.all()), [])

    def test_rotation_order_is_ordered_by_position(self):
        rule = RecurrenceRule.objects.create(frequency=RecurrenceRule.Frequency.WEEKLY)
        ben = Child.objects.create(name="Ben")
        ana = Child.objects.create(name="Ana")
        RotationSlot.objects.create(rule=rule, child=ben, position=1)
        RotationSlot.objects.create(rule=rule, child=ana, position=0)
        self.assertEqual(
            [slot.child.name for slot in rule.rotation_slots.all()], ["Ana", "Ben"]
        )

    def test_a_position_is_unique_within_a_rule(self):
        rule = RecurrenceRule.objects.create(frequency=RecurrenceRule.Frequency.WEEKLY)
        RotationSlot.objects.create(
            rule=rule, child=Child.objects.create(name="Ana"), position=0
        )
        ben = Child.objects.create(name="Ben")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RotationSlot.objects.create(rule=rule, child=ben, position=0)

    def test_a_child_appears_at_most_once_in_a_rule(self):
        rule = RecurrenceRule.objects.create(frequency=RecurrenceRule.Frequency.WEEKLY)
        ana = Child.objects.create(name="Ana")
        RotationSlot.objects.create(rule=rule, child=ana, position=0)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RotationSlot.objects.create(rule=rule, child=ana, position=1)

    def test_the_same_position_is_allowed_in_a_different_rule(self):
        ana = Child.objects.create(name="Ana")
        for _ in range(2):
            rule = RecurrenceRule.objects.create(
                frequency=RecurrenceRule.Frequency.WEEKLY
            )
            RotationSlot.objects.create(rule=rule, child=ana, position=0)
        self.assertEqual(RotationSlot.objects.count(), 2)


class ChoreModelTests(TestCase):
    def test_str_identifies_the_row_without_extra_queries(self):
        chore = Chore.objects.create(title="Empty the dishwasher")
        with self.assertNumQueries(0):
            self.assertEqual(str(chore), "Empty the dishwasher (available)")

    def test_a_title_is_the_only_required_field(self):
        chore = Chore.objects.create(title="Empty the dishwasher")
        chore = Chore.objects.get(pk=chore.pk)
        self.assertEqual(chore.notes, "")
        self.assertEqual(chore.category, Chore.Category.OTHER)
        self.assertEqual(chore.priority, Chore.Priority.NORMAL)
        self.assertEqual(chore.status, Chore.Status.AVAILABLE)
        self.assertIsNone(chore.due_at)
        self.assertIsNone(chore.reward_amount)
        self.assertIsNone(chore.assigned_child)
        self.assertIsNone(chore.recurrence_rule)
        self.assertFalse(chore.is_shared)
        self.assertEqual(chore.rejection_reason, "")

    def test_a_new_chore_is_available_with_no_milestone_timestamps(self):
        chore = Chore.objects.get(pk=Chore.objects.create(title="Sweep").pk)
        self.assertEqual(chore.status, "available")
        self.assertIsNone(chore.claimed_at)
        self.assertIsNone(chore.completed_at)
        self.assertIsNone(chore.approved_at)

    def test_bookkeeping_timestamps_are_maintained_automatically(self):
        chore = Chore.objects.create(title="Sweep")
        self.assertIsNotNone(chore.created_at)
        first_updated_at = chore.updated_at
        chore.title = "Sweep the hall"
        chore.save()
        self.assertGreaterEqual(chore.updated_at, first_updated_at)
        self.assertEqual(Chore.objects.get(pk=chore.pk).created_at, chore.created_at)

    def test_status_choices_are_exactly_the_five_workflow_states(self):
        self.assertEqual(
            [value for value, _ in Chore.Status.choices],
            ["available", "claimed", "awaiting_approval", "approved", "returned"],
        )

    def test_status_labels_are_title_cased(self):
        self.assertEqual(
            dict(Chore.Status.choices)["awaiting_approval"], "Awaiting approval"
        )

    def test_priority_choices_are_exactly_normal_and_urgent(self):
        self.assertEqual(
            [value for value, _ in Chore.Priority.choices], ["normal", "urgent"]
        )

    def test_category_choices_cover_the_spec_set(self):
        self.assertLessEqual(
            {"kitchen", "laundry", "outdoors", "other"},
            {value for value, _ in Chore.Category.choices},
        )

    def test_reward_amount_is_a_decimal_field_not_a_float_field(self):
        field = Chore._meta.get_field("reward_amount")
        self.assertEqual(field.get_internal_type(), "DecimalField")
        self.assertEqual((field.max_digits, field.decimal_places), (7, 2))

    def test_a_chore_with_no_reward_is_legal(self):
        chore = Chore.objects.create(title="Tidy your room", reward_amount=None)
        self.assertIsNone(Chore.objects.get(pk=chore.pk).reward_amount)

    def test_a_decimal_reward_round_trips_through_the_database_unchanged(self):
        chore = Chore.objects.create(title="Mow", reward_amount=Decimal("12.50"))
        stored = Chore.objects.get(pk=chore.pk).reward_amount
        self.assertIsInstance(stored, Decimal)
        self.assertEqual(stored, Decimal("12.50"))

    def test_a_negative_reward_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Chore.objects.create(title="Mow", reward_amount=Decimal("-0.01"))

    def test_a_zero_reward_is_allowed(self):
        chore = Chore.objects.create(title="Mow", reward_amount=Decimal("0.00"))
        self.assertEqual(Chore.objects.get(pk=chore.pk).reward_amount, Decimal("0.00"))

    def test_ordering_is_urgent_first_then_due_date_then_id(self):
        now = timezone.now()
        urgent_late = Chore.objects.create(
            title="Urgent late",
            priority=Chore.Priority.URGENT,
            due_at=now + datetime.timedelta(days=2),
        )
        normal_early = Chore.objects.create(
            title="Normal early", due_at=now + datetime.timedelta(days=1)
        )
        normal_undated = Chore.objects.create(title="Normal undated")
        normal_early_tiebreak = Chore.objects.create(
            title="Normal early tiebreak", due_at=now + datetime.timedelta(days=1)
        )
        urgent_early = Chore.objects.create(
            title="Urgent early",
            priority=Chore.Priority.URGENT,
            due_at=now + datetime.timedelta(days=1),
        )
        self.assertEqual(
            list(Chore.objects.all()),
            [
                urgent_early,
                urgent_late,
                normal_early,
                normal_early_tiebreak,
                normal_undated,
            ],
        )

    def test_indexes_cover_status_due_at_and_child_status(self):
        indexed = {tuple(index.fields) for index in Chore._meta.indexes}
        self.assertIn(("status",), indexed)
        self.assertIn(("due_at",), indexed)
        self.assertIn(("assigned_child", "status"), indexed)

    def test_deleting_a_child_with_an_assigned_chore_is_protected(self):
        ana = Child.objects.create(name="Ana")
        Chore.objects.create(title="Sweep", assigned_child=ana)
        with self.assertRaises(ProtectedError):
            ana.delete()
        self.assertTrue(Child.objects.filter(pk=ana.pk).exists())

    def test_deleting_a_recurrence_rule_leaves_its_chores_as_one_off(self):
        rule = RecurrenceRule.objects.create(frequency=RecurrenceRule.Frequency.DAILY)
        chore = Chore.objects.create(title="Sweep", recurrence_rule=rule)
        rule.delete()
        chore = Chore.objects.get(pk=chore.pk)
        self.assertIsNone(chore.recurrence_rule)


class PayoutModelTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")

    def test_str_includes_the_child_and_the_amount(self):
        payout = Payout.objects.create(
            child=self.ana, amount=Decimal("12.50"), paid_on=datetime.date(2026, 1, 5)
        )
        self.assertEqual(str(payout), "Ana: 12.50")

    def test_amount_is_a_two_place_decimal(self):
        field = Payout._meta.get_field("amount")
        self.assertEqual(field.get_internal_type(), "DecimalField")
        self.assertEqual((field.max_digits, field.decimal_places), (7, 2))

    def test_paid_on_is_a_date_not_a_timestamp(self):
        self.assertEqual(
            Payout._meta.get_field("paid_on").get_internal_type(), "DateField"
        )

    def test_created_at_is_set_automatically(self):
        payout = Payout.objects.create(
            child=self.ana, amount=Decimal("1.00"), paid_on=datetime.date(2026, 1, 5)
        )
        self.assertIsNotNone(payout.created_at)

    def test_a_zero_amount_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payout.objects.create(
                    child=self.ana,
                    amount=Decimal("0.00"),
                    paid_on=datetime.date(2026, 1, 5),
                )

    def test_a_negative_amount_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payout.objects.create(
                    child=self.ana,
                    amount=Decimal("-5.00"),
                    paid_on=datetime.date(2026, 1, 5),
                )

    def test_deleting_a_child_with_payouts_raises_protected_error(self):
        Payout.objects.create(
            child=self.ana, amount=Decimal("5.00"), paid_on=datetime.date(2026, 1, 5)
        )
        with self.assertRaises(ProtectedError):
            self.ana.delete()
        self.assertTrue(Child.objects.filter(pk=self.ana.pk).exists())
        self.assertEqual(Payout.objects.count(), 1)

    def test_ordering_is_newest_first_with_an_id_tiebreak(self):
        older = Payout.objects.create(
            child=self.ana, amount=Decimal("1.00"), paid_on=datetime.date(2026, 1, 1)
        )
        newer_first = Payout.objects.create(
            child=self.ana, amount=Decimal("2.00"), paid_on=datetime.date(2026, 1, 5)
        )
        newer_second = Payout.objects.create(
            child=self.ana, amount=Decimal("3.00"), paid_on=datetime.date(2026, 1, 5)
        )
        self.assertEqual(list(Payout.objects.all()), [newer_second, newer_first, older])

    def test_index_covers_child_and_paid_on(self):
        indexed = {tuple(index.fields) for index in Payout._meta.indexes}
        self.assertIn(("child", "paid_on"), indexed)


class ChoreRequestModelTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")

    def test_str_includes_the_requesting_child_and_the_title(self):
        request = ChoreRequest.objects.create(
            requested_by=self.ana, title="Wash the car"
        )
        self.assertEqual(str(request), "Ana: Wash the car")

    def test_defaults_are_pending_with_no_resulting_chore(self):
        request = ChoreRequest.objects.create(
            requested_by=self.ana, title="Wash the car"
        )
        request = ChoreRequest.objects.get(pk=request.pk)
        self.assertEqual(request.status, "pending")
        self.assertEqual(request.notes, "")
        self.assertIsNone(request.resulting_chore)
        self.assertIsNotNone(request.created_at)

    def test_status_choices_are_exactly_pending_accepted_declined(self):
        self.assertEqual(
            [value for value, _ in ChoreRequest.Status.choices],
            ["pending", "accepted", "declined"],
        )

    def test_deleting_the_requesting_child_cascades(self):
        ChoreRequest.objects.create(requested_by=self.ana, title="Wash the car")
        self.ana.delete()
        self.assertEqual(ChoreRequest.objects.count(), 0)

    def test_deleting_the_resulting_chore_leaves_the_request(self):
        chore = Chore.objects.create(title="Wash the car")
        request = ChoreRequest.objects.create(
            requested_by=self.ana, title="Wash the car", resulting_chore=chore
        )
        chore.delete()
        request = ChoreRequest.objects.get(pk=request.pk)
        self.assertIsNone(request.resulting_chore)

    def test_ordering_is_newest_first(self):
        first = ChoreRequest.objects.create(requested_by=self.ana, title="First")
        second = ChoreRequest.objects.create(requested_by=self.ana, title="Second")
        self.assertEqual(list(ChoreRequest.objects.all()), [second, first])

    def test_index_covers_status(self):
        indexed = {tuple(index.fields) for index in ChoreRequest._meta.indexes}
        self.assertIn(("status",), indexed)


DOMAIN_MODELS = [Child, Chore, ChoreRequest, Payout, RecurrenceRule]


def admin_url(model, page):
    return reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_{page}")


class AdminRegistrationTests(TestCase):
    """The five domain models are reachable and usable in the default admin."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = get_user_model().objects.create_superuser(
            username="parent", email="parent@example.com", password="not-a-secret"
        )

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_all_five_models_are_registered(self):
        for model in DOMAIN_MODELS:
            with self.subTest(model=model.__name__):
                self.assertIn(model, django_admin.site._registry)

    def test_admin_index_links_to_every_model(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        for model in DOMAIN_MODELS:
            with self.subTest(model=model.__name__):
                self.assertContains(response, admin_url(model, "changelist"))

    def test_changelists_return_200(self):
        for model in DOMAIN_MODELS:
            with self.subTest(model=model.__name__):
                response = self.client.get(admin_url(model, "changelist"))
                self.assertEqual(response.status_code, 200)

    def test_add_forms_return_200(self):
        for model in DOMAIN_MODELS:
            with self.subTest(model=model.__name__):
                response = self.client.get(admin_url(model, "add"))
                self.assertEqual(response.status_code, 200)

    def test_registration_is_plain(self):
        for model in DOMAIN_MODELS:
            with self.subTest(model=model.__name__):
                model_admin = django_admin.site._registry[model]
                self.assertEqual(tuple(model_admin.list_filter), ())
                self.assertEqual(tuple(model_admin.search_fields), ())
                self.assertEqual(tuple(model_admin.actions or ()), ())

    def test_a_chore_is_created_through_the_admin_add_form_with_only_a_title(self):
        url = admin_url(Chore, "add")
        add_page = self.client.get(url)
        self.assertEqual(add_page.status_code, 200)

        # Post the form the way a parent who typed a title and left every
        # pre-filled default alone would: each field at its initial value.
        form = add_page.context["adminform"].form
        data = {
            name: "" if field.initial is None else field.initial
            for name, field in form.fields.items()
        }
        data["title"] = "Empty the dishwasher"

        response = self.client.post(url, data, follow=True)
        self.assertEqual(response.status_code, 200)
        errors = (
            response.context["adminform"].form.errors
            if "adminform" in response.context
            else None
        )
        self.assertEqual(
            Chore.objects.count(), 1, f"admin add form did not save: {errors}"
        )
        chore = Chore.objects.get()
        self.assertEqual(chore.title, "Empty the dishwasher")
        self.assertEqual(chore.status, Chore.Status.AVAILABLE)
        self.assertIsNone(chore.reward_amount)
        self.assertIsNone(chore.assigned_child)
