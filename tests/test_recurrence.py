from datetime import datetime, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chores.models import Child, Chore, RecurrenceRule, RotationSlot
from chores.recurrence import (
    advance_recurring_chore,
    next_due_at,
    process_due_recurring_chores,
)


class RecurrenceEditorTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.ben = Child.objects.create(name="Ben")
        self.cara = Child.objects.create(name="Cara")
        self.url = reverse("chores:chore_create")

    def data(self, **overrides):
        data = {
            "title": "Feed the pets",
            "notes": "Morning food",
            "category": Chore.Category.OTHER,
            "priority": Chore.Priority.NORMAL,
            "due_at": "2099-01-05T09:00",
            "reward_amount": "3.00",
            "assigned_child": "",
            "is_shared": "",
            "is_recurring": "on",
            "recurrence_frequency": RecurrenceRule.Frequency.WEEKLY,
            "recurrence_interval": "1",
            "recurrence_weekdays": "",
            "rotation_order": f"{self.ben.pk},{self.ana.pk},{self.cara.pk}",
        }
        data.update(overrides)
        return data

    def test_parent_create_persists_rule_and_ordered_rotation(self):
        response = self.client.post(self.url, self.data())

        self.assertEqual(response.status_code, 302)
        chore = Chore.objects.get()
        rule = chore.recurrence_rule
        self.assertEqual(rule.frequency, RecurrenceRule.Frequency.WEEKLY)
        self.assertEqual(
            list(rule.rotation_slots.values_list("child_id", flat=True)),
            [self.ben.pk, self.ana.pk, self.cara.pk],
        )
        self.assertEqual(chore.assigned_child_id, self.ben.pk)

    def test_duplicate_rotation_child_is_rejected_without_partial_rows(self):
        response = self.client.post(
            self.url, self.data(rotation_order=f"{self.ana.pk},{self.ana.pk}")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "distinct child ids")
        self.assertEqual(Chore.objects.count(), 0)
        self.assertEqual(RecurrenceRule.objects.count(), 0)

    def test_custom_schedule_requires_valid_weekdays(self):
        response = self.client.post(
            self.url,
            self.data(
                recurrence_frequency=RecurrenceRule.Frequency.CUSTOM,
                recurrence_weekdays="",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least one weekday")
        self.assertEqual(RecurrenceRule.objects.count(), 0)


class RecurrenceServiceTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.ben = Child.objects.create(name="Ben")
        self.cara = Child.objects.create(name="Cara")
        self.due = timezone.make_aware(datetime(2099, 1, 5, 9, 0))

    def rule(self, frequency=RecurrenceRule.Frequency.WEEKLY, interval=1, weekdays=""):
        rule = RecurrenceRule.objects.create(
            frequency=frequency,
            interval=interval,
            weekdays=weekdays,
        )
        for position, child in enumerate((self.ana, self.ben, self.cara)):
            RotationSlot.objects.create(rule=rule, child=child, position=position)
        rule.next_rotation_position = 1
        rule.save(update_fields=["next_rotation_position"])
        return rule

    def recurring_chore(self, rule):
        return Chore.objects.create(
            title="Feed the pets",
            due_at=self.due,
            reward_amount="3.00",
            assigned_child=self.ana,
            recurrence_rule=rule,
            status=Chore.Status.APPROVED,
        )

    def test_schedule_advancement_handles_daily_weekly_and_custom(self):
        daily = RecurrenceRule.objects.create(
            frequency=RecurrenceRule.Frequency.DAILY, interval=2
        )
        weekly = RecurrenceRule.objects.create(
            frequency=RecurrenceRule.Frequency.WEEKLY, interval=3
        )
        custom = RecurrenceRule.objects.create(
            frequency=RecurrenceRule.Frequency.CUSTOM,
            weekdays="0,2",
        )

        self.assertEqual(next_due_at(self.due, daily), self.due + timedelta(days=2))
        self.assertEqual(next_due_at(self.due, weekly), self.due + timedelta(weeks=3))
        self.assertEqual(
            next_due_at(self.due, custom),
            self.due + timedelta(days=2),
        )

    def test_approval_generates_one_next_occurrence_and_rotates_fairly(self):
        rule = self.rule()
        current = Chore.objects.create(
            title="Feed the pets",
            due_at=self.due,
            assigned_child=self.ana,
            recurrence_rule=rule,
        )

        children = []
        for expected in (self.ben, self.cara, self.ana):
            current.claim(current.assigned_child)
            current.complete()
            current.approve()
            current = current.next_occurrences.get()
            children.append(current.assigned_child_id)

        self.assertEqual(children, [self.ben.pk, self.cara.pk, self.ana.pk])
        self.assertEqual(Chore.objects.filter(recurrence_rule=rule).count(), 4)

    def test_advancing_the_same_source_is_idempotent(self):
        rule = self.rule()
        source = self.recurring_chore(rule)

        first = advance_recurring_chore(source)
        second = advance_recurring_chore(source)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Chore.objects.filter(recurrence_rule=rule).count(), 2)

    def test_empty_rotation_does_not_generate_an_occurrence(self):
        rule = RecurrenceRule.objects.create(
            frequency=RecurrenceRule.Frequency.DAILY,
            interval=1,
        )
        source = self.recurring_chore(rule)
        self.assertIsNone(advance_recurring_chore(source))
        self.assertEqual(Chore.objects.count(), 1)

    def test_due_processing_advances_only_due_approved_sources(self):
        rule = self.rule()
        due = self.recurring_chore(rule)
        future = Chore.objects.create(
            title="Later",
            due_at=self.due + timedelta(days=4),
            assigned_child=self.ana,
            recurrence_rule=rule,
            status=Chore.Status.APPROVED,
        )

        generated = process_due_recurring_chores(now=self.due + timedelta(days=1))

        self.assertEqual([item.pk for item in generated], [due.next_occurrences.get().pk])
        self.assertFalse(future.next_occurrences.exists())
