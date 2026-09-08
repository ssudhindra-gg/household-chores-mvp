from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chores.limits import weekly_earned, weekly_limit_warning
from chores.models import Child, Chore


class WeeklyLimitTests(TestCase):
    def setUp(self):
        self.child = Child.objects.create(
            name="Ana", weekly_earning_limit=Decimal("10.00")
        )
        self.user = get_user_model().objects.create_superuser(
            username="parent", email="parent@example.com", password="password"
        )
        self.client.force_login(self.user)
        self.changelist_url = reverse("admin:chores_chore_changelist")

    def approved_reward(self, amount, approved_at):
        return Chore.objects.create(
            title="Completed",
            assigned_child=self.child,
            reward_amount=amount,
            status=Chore.Status.APPROVED,
            approved_at=approved_at,
        )

    def awaiting_reward(self, amount):
        chore = Chore.objects.create(
            title="New chore",
            assigned_child=self.child,
            reward_amount=amount,
        )
        chore.claim(self.child)
        chore.complete()
        return chore

    def test_current_week_aggregation_excludes_other_weeks(self):
        now = timezone.now()
        self.approved_reward("6.00", now - timedelta(days=1))
        self.approved_reward("8.00", now - timedelta(days=14))

        self.assertEqual(weekly_earned(self.child, now), Decimal("6.00"))

    def test_warning_is_none_for_unrewarded_or_unlimited_chores(self):
        now = timezone.now()
        chore = self.awaiting_reward(None)
        self.assertIsNone(weekly_limit_warning(chore, now))

        self.child.weekly_earning_limit = None
        self.child.save(update_fields=["weekly_earning_limit"])
        chore.reward_amount = Decimal("20.00")
        self.assertIsNone(weekly_limit_warning(chore, now))

    def test_admin_warning_is_soft_and_appears_at_the_limit(self):
        now = timezone.now()
        self.approved_reward("7.50", now)
        chore = self.awaiting_reward("2.50")

        response = self.client.post(
            self.changelist_url,
            {"action": "approve_selected", "_selected_action": chore.pk},
        )

        self.assertEqual(response.status_code, 302)
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.APPROVED)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(
            any("at the limit" in message for message in messages), messages
        )

    def test_below_limit_has_no_warning(self):
        now = timezone.now()
        self.approved_reward("6.00", now)
        chore = self.awaiting_reward("2.50")

        self.assertIsNone(weekly_limit_warning(chore, now))
