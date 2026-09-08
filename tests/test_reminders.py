from datetime import datetime, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chores.models import Child, Chore
from chores.reminders import OVERDUE, UPCOMING, reminder_state


class ReminderStateTests(TestCase):
    def setUp(self):
        self.now = timezone.make_aware(datetime(2099, 1, 5, 12, 0))

    def test_due_dates_are_classified_against_the_render_time(self):
        upcoming = Chore(due_at=self.now + timedelta(minutes=1))
        overdue = Chore(due_at=self.now - timedelta(minutes=1))
        at_now = Chore(due_at=self.now)

        self.assertEqual(reminder_state(upcoming, self.now), UPCOMING)
        self.assertEqual(reminder_state(overdue, self.now), OVERDUE)
        self.assertEqual(reminder_state(at_now, self.now), OVERDUE)

    def test_no_due_date_has_no_reminder_state(self):
        self.assertIsNone(reminder_state(Chore(), self.now))

    def test_board_renders_badges_in_parent_and_kid_modes(self):
        render_now = timezone.now()
        Chore.objects.create(title="Soon", due_at=render_now + timedelta(days=1))
        Chore.objects.create(title="Late", due_at=render_now - timedelta(days=1))
        Chore.objects.create(title="Whenever", due_at=None)

        parent = self.client.get(reverse("chores:family_board"))
        self.assertContains(parent, "Upcoming reminder")
        self.assertContains(parent, "Overdue reminder")
        self.assertContains(parent, "Whenever")

        child = Child.objects.create(name="Ana")
        self.client.post(
            reverse("chores:mode_select"), {"mode": "kid", "child": child.pk}
        )
        kid = self.client.get(reverse("chores:family_board"))
        self.assertContains(kid, "Upcoming reminder")
        self.assertContains(kid, "Overdue reminder")
