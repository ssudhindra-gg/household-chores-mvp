"""Tests for the chore activity history (issue #18).

Scope: the append-only ``ChoreEvent`` row every successful status transition
writes, where it is written from, what it records, and the read-only panel
that shows it on the chore's Django Admin page.

Rows are re-fetched from the database before asserting, so these tests prove
the event was stored rather than only built in memory.
"""

import ast
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from chores import activity, state_machine
from chores.admin import ACTIVITY_PREDATES_LOG, NO_ACTIVITY_YET, ChoreEventInline
from chores.modes import Mode
from chores.models import (
    Child,
    Chore,
    ChoreEvent,
    ChoreEventImmutable,
    RecurrenceRule,
    RotationSlot,
)
from chores.recurrence import RecurrenceError
from chores.state_machine import (
    APPROVE,
    APPROVED,
    AVAILABLE,
    AWAITING_APPROVAL,
    CLAIM,
    CLAIMED,
    COMPLETE,
    KID,
    PARENT,
    REJECT,
    RELEASE,
    RETURNED,
    SYSTEM,
    InvalidChoreTransition,
    RejectionReasonRequired,
)

APP_DIR = Path(state_machine.__file__).resolve().parent

EVENT_FIELDS = (
    "action",
    "from_status",
    "to_status",
    "actor_mode",
    "acting_child_id",
    "reason",
    "occurred_at",
)


def stored(event):
    """The event's columns, re-read from the database."""
    fresh = ChoreEvent.objects.get(pk=event.pk)
    return {field: getattr(fresh, field) for field in EVENT_FIELDS}


class ActivityTestCase(TestCase):
    """Shared fixtures."""

    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.bob = Child.objects.create(name="Bob")

    def make_chore(self, **kwargs):
        kwargs.setdefault("title", "Dishes")
        return Chore.objects.create(**kwargs)

    def claimed_chore(self, child=None, **kwargs):
        chore = self.make_chore(**kwargs)
        chore.claim(child or self.ana)
        return chore

    def awaiting_chore(self, child=None, **kwargs):
        chore = self.claimed_chore(child, **kwargs)
        chore.complete()
        return chore

    def only_event(self, chore):
        events = list(chore.events.all())
        self.assertEqual(len(events), 1, events)
        return events[0]

    def history(self, chore):
        """This chore's events oldest first, as ``(action, from, to)``."""
        return [
            (event.action, event.from_status, event.to_status)
            for event in chore.events.order_by("occurred_at", "id")
        ]


# --- The model itself ----------------------------------------------------


class ChoreEventModelTests(ActivityTestCase):
    def test_actor_constants_match_the_text_choices_set(self):
        # The same drift guard #3 uses for the statuses: the state machine
        # holds plain strings so it need not import the models module, so
        # something has to prove the two sets stay in step.
        self.assertEqual(
            set(state_machine.ACTOR_MODES), {c.value for c in ChoreEvent.ActorMode}
        )
        self.assertEqual(state_machine.ACTOR_MODES, (PARENT, KID, SYSTEM))

    def test_str_identifies_the_row_from_local_columns_only(self):
        chore = self.claimed_chore()
        event = self.only_event(chore)

        with self.assertNumQueries(0):
            self.assertEqual(str(event), f"{CLAIM}: {AVAILABLE} -> {CLAIMED}")

    def test_occurred_at_is_not_auto_now_add(self):
        field = ChoreEvent._meta.get_field("occurred_at")
        self.assertFalse(getattr(field, "auto_now_add", False))
        self.assertFalse(getattr(field, "auto_now", False))

    def test_migration_creates_the_table_without_inserting_rows(self):
        self.assertEqual(ChoreEvent.objects.count(), 0)

    def test_no_migration_is_outstanding(self):
        # Guards the one committed migration against a model edit that never
        # got one of its own.
        try:
            call_command("makemigrations", "chores", check=True, dry_run=True)
        except (CommandError, SystemExit) as exc:  # pragma: no cover - failure path
            self.fail(f"chores has an unmade migration: {exc}")

    def test_deleting_the_acting_child_is_protected(self):
        from django.db.models import ProtectedError

        chore = self.claimed_chore()

        with self.assertRaises(ProtectedError):
            self.ana.delete()

        self.assertEqual(chore.events.count(), 1)


# --- Append-only ---------------------------------------------------------


class ChoreEventImmutabilityTests(ActivityTestCase):
    def setUp(self):
        super().setUp()
        self.chore = self.claimed_chore()
        self.event = self.only_event(self.chore)
        self.before = stored(self.event)

    def assertUnchanged(self):
        self.assertEqual(stored(self.event), self.before)

    def test_saving_an_existing_event_is_refused(self):
        self.event.reason = "rewritten"

        with self.assertRaises(ChoreEventImmutable):
            self.event.save()

        self.assertUnchanged()

    def test_deleting_an_event_is_refused(self):
        with self.assertRaises(ChoreEventImmutable):
            self.event.delete()

        self.assertUnchanged()
        self.assertEqual(ChoreEvent.objects.count(), 1)

    def test_bulk_update_is_refused(self):
        with self.assertRaises(ChoreEventImmutable):
            ChoreEvent.objects.filter(pk=self.event.pk).update(reason="")

        self.assertUnchanged()

    def test_bulk_delete_is_refused(self):
        with self.assertRaises(ChoreEventImmutable):
            ChoreEvent.objects.filter(pk=self.event.pk).delete()

        self.assertUnchanged()
        self.assertEqual(ChoreEvent.objects.count(), 1)

    def test_deleting_the_chore_is_the_one_way_its_events_go(self):
        other = self.claimed_chore(self.bob, title="Bins")
        other_event_ids = set(other.events.values_list("pk", flat=True))

        self.chore.delete()

        self.assertFalse(ChoreEvent.objects.filter(pk=self.event.pk).exists())
        self.assertEqual(
            set(ChoreEvent.objects.values_list("pk", flat=True)), other_event_ids
        )


# --- Written from the state machine, and only from there -----------------


class WrittenFromTheStateMachineTests(ActivityTestCase):
    def test_each_action_writes_the_event_the_transition_table_describes(self):
        chore = self.make_chore()

        chore.claim(self.ana)
        self.assertEqual(
            self.history(chore)[-1:], [(CLAIM, AVAILABLE, CLAIMED)]
        )

        chore.complete()
        self.assertEqual(
            self.history(chore)[-1:], [(COMPLETE, CLAIMED, AWAITING_APPROVAL)]
        )

        chore.reject("Missed the pans")
        self.assertEqual(
            self.history(chore)[-1:], [(REJECT, AWAITING_APPROVAL, RETURNED)]
        )

        chore.release()
        self.assertEqual(self.history(chore)[-1:], [(RELEASE, RETURNED, AVAILABLE)])

        chore.claim(self.ana)
        chore.complete()
        chore.approve()
        self.assertEqual(
            self.history(chore)[-1:], [(APPROVE, AWAITING_APPROVAL, APPROVED)]
        )

    def test_claim_event_names_the_claiming_child(self):
        chore = self.make_chore()

        chore.claim(self.bob, actor_mode=KID)

        self.assertEqual(
            stored(self.only_event(chore)),
            {
                "action": CLAIM,
                "from_status": AVAILABLE,
                "to_status": CLAIMED,
                "actor_mode": KID,
                "acting_child_id": self.bob.pk,
                "reason": "",
                "occurred_at": Chore.objects.get(pk=chore.pk).claimed_at,
            },
        )

    def test_complete_event_is_stored_with_an_empty_reason(self):
        chore = self.claimed_chore()

        chore.complete()

        event = chore.events.order_by("occurred_at", "id").last()
        self.assertEqual(stored(event)["reason"], "")
        self.assertEqual(stored(event)["action"], COMPLETE)

    def test_approve_event_matches_the_approved_at_instant(self):
        chore = self.awaiting_chore()

        chore.approve()

        chore.refresh_from_db()
        self.assertEqual(
            chore.approved_at, chore.events.get(action=APPROVE).occurred_at
        )

    def test_reject_event_stores_the_stripped_reason(self):
        chore = self.awaiting_chore()

        chore.reject("   Sink still full   ")

        chore.refresh_from_db()
        event = stored(chore.events.get(action=REJECT))
        self.assertEqual(event["reason"], "Sink still full")
        self.assertEqual(event["reason"], chore.rejection_reason)

    def test_release_event_names_the_child_who_was_on_it(self):
        chore = self.claimed_chore(self.bob)

        chore.release(actor_mode=KID)

        chore.refresh_from_db()
        self.assertIsNone(chore.assigned_child_id)
        event = stored(chore.events.get(action=RELEASE))
        self.assertEqual(event["acting_child_id"], self.bob.pk)
        self.assertEqual(event["from_status"], CLAIMED)
        self.assertEqual(event["to_status"], AVAILABLE)

    def test_every_milestone_timestamp_equals_its_event(self):
        chore = self.make_chore()
        chore.claim(self.ana)
        chore.complete()
        chore.approve()

        chore.refresh_from_db()
        self.assertEqual(chore.claimed_at, chore.events.get(action=CLAIM).occurred_at)
        self.assertEqual(
            chore.completed_at, chore.events.get(action=COMPLETE).occurred_at
        )
        self.assertEqual(
            chore.approved_at, chore.events.get(action=APPROVE).occurred_at
        )

    def test_a_full_rejected_then_redone_run_keeps_all_six_events(self):
        chore = self.make_chore()
        chore.claim(self.ana)
        chore.complete()
        chore.reject("Not dry")
        chore.claim(self.ana)
        chore.complete()
        chore.approve()

        self.assertEqual(
            self.history(chore),
            [
                (CLAIM, AVAILABLE, CLAIMED),
                (COMPLETE, CLAIMED, AWAITING_APPROVAL),
                (REJECT, AWAITING_APPROVAL, RETURNED),
                (CLAIM, RETURNED, CLAIMED),
                (COMPLETE, CLAIMED, AWAITING_APPROVAL),
                (APPROVE, AWAITING_APPROVAL, APPROVED),
            ],
        )

    def test_two_rejections_keep_both_reasons_though_the_chore_keeps_one(self):
        chore = self.awaiting_chore()
        chore.reject("Missed the pans")
        chore.claim(self.ana)
        chore.complete()
        chore.reject("Still greasy")

        reasons = [
            event.reason
            for event in chore.events.filter(action=REJECT).order_by("occurred_at", "id")
        ]
        self.assertEqual(reasons, ["Missed the pans", "Still greasy"])

        chore.refresh_from_db()
        self.assertEqual(chore.rejection_reason, "Still greasy")

    def test_n_successful_transitions_produce_exactly_n_events(self):
        chore = self.make_chore()
        for step in (
            lambda: chore.claim(self.ana),
            lambda: chore.complete(),
            lambda: chore.reject("Again"),
            lambda: chore.release(),
        ):
            step()

        self.assertEqual(chore.events.count(), 4)
        self.assertEqual(ChoreEvent.objects.count(), 4)

    def test_a_mixed_run_records_one_event_per_success_and_none_per_refusal(self):
        chore = self.make_chore()

        chore.claim(self.ana)
        with self.assertRaises(InvalidChoreTransition):
            chore.approve()
        chore.complete()
        with self.assertRaises(InvalidChoreTransition):
            chore.claim(self.bob)
        with self.assertRaises(RejectionReasonRequired):
            chore.reject("   ")
        chore.approve()

        self.assertEqual(chore.events.count(), 3)
        self.assertEqual(
            [event.action for event in chore.events.order_by("occurred_at", "id")],
            [CLAIM, COMPLETE, APPROVE],
        )

    def test_illegal_transitions_write_no_event(self):
        for action, builder in (
            (COMPLETE, self.make_chore),
            (APPROVE, self.make_chore),
            (REJECT, self.make_chore),
            (RELEASE, self.make_chore),
            (CLAIM, self.awaiting_chore),
            (COMPLETE, self.awaiting_chore),
        ):
            with self.subTest(action=action):
                chore = builder()
                before = ChoreEvent.objects.filter(chore=chore).count()
                with self.assertRaises(InvalidChoreTransition):
                    state_machine.apply_transition(
                        chore, action, child=self.ana, reason="because"
                    )
                self.assertEqual(
                    ChoreEvent.objects.filter(chore=chore).count(), before
                )

    def test_an_illegal_transition_on_a_fresh_chore_leaves_the_table_empty(self):
        chore = self.make_chore()

        for action in (COMPLETE, APPROVE, REJECT, RELEASE):
            with self.subTest(action=action):
                with self.assertRaises(InvalidChoreTransition):
                    state_machine.apply_transition(chore, action, reason="because")

        self.assertEqual(ChoreEvent.objects.count(), 0)

    def test_a_missing_rejection_reason_writes_no_event(self):
        chore = self.awaiting_chore()
        before = chore.events.count()

        for reason in (None, "", "   ", "\n\t"):
            with self.subTest(reason=reason):
                with self.assertRaises(RejectionReasonRequired):
                    chore.reject(reason)

        self.assertEqual(chore.events.count(), before)
        self.assertFalse(chore.events.filter(action=REJECT).exists())

    def test_a_failing_event_write_rolls_the_status_back(self):
        chore = self.claimed_chore()
        before = Chore.objects.get(pk=chore.pk).status
        count = ChoreEvent.objects.count()

        with mock.patch.object(
            activity, "record_event", side_effect=RuntimeError("no disk")
        ):
            with self.assertRaises(RuntimeError):
                chore.complete()

        self.assertEqual(Chore.objects.get(pk=chore.pk).status, before)
        self.assertEqual(ChoreEvent.objects.count(), count)

    def test_a_failing_recurrence_rolls_back_both_the_status_and_the_event(self):
        rule = RecurrenceRule.objects.create(
            frequency=RecurrenceRule.Frequency.DAILY, interval=1
        )
        RotationSlot.objects.create(rule=rule, child=self.ana, position=0)
        # No ``due_at``, so ``advance_recurring_chore`` raises after the
        # status has been saved inside ``Chore.approve()``'s atomic block.
        chore = self.awaiting_chore(recurrence_rule=rule, due_at=None)
        before = Chore.objects.get(pk=chore.pk).status
        count = ChoreEvent.objects.count()

        with self.assertRaises(RecurrenceError):
            chore.approve()

        self.assertEqual(Chore.objects.get(pk=chore.pk).status, before)
        self.assertEqual(ChoreEvent.objects.count(), count)
        self.assertFalse(ChoreEvent.objects.filter(action=APPROVE).exists())

    def test_the_writer_is_table_driven_and_needs_no_edit_for_a_sixth_row(self):
        # Stands in for #21's ``approved -> awaiting_approval`` undo: a new
        # row in TRANSITIONS must be recorded without touching the writer.
        chore = self.awaiting_chore()
        chore.approve()
        undo = "undo"

        with mock.patch.dict(
            state_machine.TRANSITIONS, {undo: {APPROVED: AWAITING_APPROVAL}}
        ), mock.patch.dict(state_machine.ACTION_TARGETS, {undo: AWAITING_APPROVAL}):
            state_machine.apply_transition(chore, undo, actor_mode=PARENT)

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.AWAITING_APPROVAL)
        event = stored(chore.events.get(action=undo))
        self.assertEqual(event["from_status"], APPROVED)
        self.assertEqual(event["to_status"], AWAITING_APPROVAL)
        self.assertEqual(event["actor_mode"], PARENT)


# --- Module hygiene ------------------------------------------------------


class ActivityModuleHygieneTests(TestCase):
    def test_activity_imports_the_model_inside_the_function_body(self):
        tree = ast.parse((APP_DIR / "activity.py").read_text(encoding="utf-8"))

        module_level = [
            node
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        self.assertEqual(module_level, [])

        inner = [
            node.module
            for function in tree.body
            if isinstance(function, ast.FunctionDef)
            for node in ast.walk(function)
            if isinstance(node, ast.ImportFrom)
        ]
        self.assertIn("chores.models", inner)

    def test_activity_restates_no_transition_rule(self):
        # Prose may mention the table; code may not touch it. Names only,
        # so the module docstring is not what is being asserted about.
        tree = ast.parse((APP_DIR / "activity.py").read_text(encoding="utf-8"))
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }

        self.assertNotIn("TRANSITIONS", names)
        self.assertNotIn("ACTION_TARGETS", names)
        self.assertNotIn("state_machine", names)
        # It never assigns a chore's status either.
        self.assertNotIn("status", names)

    def test_the_state_machine_reaches_the_model_only_through_activity(self):
        source = (APP_DIR / "state_machine.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

        self.assertIn("chores.activity", imported)
        self.assertNotIn("chores.models", imported)
        self.assertNotIn("chores.modes", imported)


# --- The actor -----------------------------------------------------------


class ActorTests(ActivityTestCase):
    def test_a_transition_with_no_actor_mode_is_recorded_as_the_system(self):
        chore = self.claimed_chore()

        event = stored(self.only_event(chore))
        self.assertEqual(event["actor_mode"], SYSTEM)
        self.assertEqual(event["acting_child_id"], self.ana.pk)

        chore.complete()
        follow_up = stored(chore.events.get(action=COMPLETE))
        self.assertEqual(follow_up["actor_mode"], SYSTEM)
        self.assertIsNone(follow_up["acting_child_id"])

    def test_a_parent_action_records_no_acting_child(self):
        chore = self.awaiting_chore()

        chore.approve(actor_mode=PARENT)

        event = stored(chore.events.get(action=APPROVE))
        self.assertEqual(event["actor_mode"], PARENT)
        self.assertIsNone(event["acting_child_id"])

    def test_a_kid_action_records_the_child_already_on_the_chore(self):
        chore = self.claimed_chore(self.bob)

        chore.complete(actor_mode=KID)

        event = stored(chore.events.get(action=COMPLETE))
        self.assertEqual(event["actor_mode"], KID)
        self.assertEqual(event["acting_child_id"], self.bob.pk)

    def test_a_mode_enum_member_is_normalised_to_its_plain_string(self):
        chore = self.make_chore()

        chore.claim(self.ana, actor_mode=Mode.KID)

        event = stored(self.only_event(chore))
        self.assertEqual(event["actor_mode"], "kid")
        self.assertNotEqual(event["actor_mode"], str(Mode.KID))

    def test_an_unknown_actor_mode_writes_nothing_at_all(self):
        chore = self.make_chore()

        for bad in ("teacher", "", None, 1, Mode):
            with self.subTest(actor_mode=bad):
                with self.assertRaises(ValueError):
                    chore.claim(self.ana, actor_mode=bad)

        self.assertEqual(Chore.objects.get(pk=chore.pk).status, Chore.Status.AVAILABLE)
        self.assertEqual(ChoreEvent.objects.count(), 0)

    def test_the_state_machine_never_refuses_a_transition_over_its_actor(self):
        for mode in state_machine.ACTOR_MODES:
            with self.subTest(actor_mode=mode):
                chore = self.make_chore(title=f"Dishes {mode}")
                chore.claim(self.ana, actor_mode=mode)
                self.assertEqual(chore.status, Chore.Status.CLAIMED)


class ActorCallSiteTests(ActivityTestCase):
    """Every transition call site inside ``chores/`` names its mode."""

    def setUp(self):
        super().setUp()
        self.mode_url = reverse("chores:mode_select")

    def enter_kid(self, child=None):
        self.client.post(
            self.mode_url, {"mode": "kid", "child": (child or self.ana).pk}
        )

    def enter_parent(self):
        self.client.post(self.mode_url, {"mode": "parent"})

    def test_the_board_claim_view_records_kid_and_the_acting_child(self):
        self.enter_kid(self.bob)
        chore = Chore.objects.create(title="Dishes")

        response = self.client.post(
            reverse("chores:claim_chore", args=[chore.pk]), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        event = stored(chore.events.get(action=CLAIM))
        self.assertEqual(event["actor_mode"], "kid")
        self.assertEqual(event["acting_child_id"], self.bob.pk)

    def test_the_board_complete_view_records_kid(self):
        self.enter_kid()
        chore = Chore.objects.create(title="Dishes")
        self.client.post(
            reverse("chores:claim_chore", args=[chore.pk]), HTTP_HX_REQUEST="true"
        )

        self.client.post(
            reverse("chores:complete_chore", args=[chore.pk]), HTTP_HX_REQUEST="true"
        )

        event = stored(chore.events.get(action=COMPLETE))
        self.assertEqual(event["actor_mode"], "kid")
        self.assertEqual(event["acting_child_id"], self.ana.pk)

    def test_the_approve_view_records_parent(self):
        chore = self.awaiting_chore()
        self.enter_parent()

        self.client.post(reverse("chores:approve_chore", args=[chore.pk]))

        event = stored(chore.events.get(action=APPROVE))
        self.assertEqual(event["actor_mode"], "parent")
        self.assertIsNone(event["acting_child_id"])

    def test_the_reject_view_records_parent(self):
        chore = self.awaiting_chore()
        self.enter_parent()

        self.client.post(
            reverse("chores:reject_chore", args=[chore.pk]), {"reason": "Too wet"}
        )

        event = stored(chore.events.get(action=REJECT))
        self.assertEqual(event["actor_mode"], "parent")
        self.assertEqual(event["reason"], "Too wet")


class AdminActionActorTests(ActivityTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            username="parent", email="parent@example.com", password="password"
        )
        self.client.force_login(self.user)
        self.changelist_url = reverse("admin:chores_chore_changelist")

    def test_approve_selected_records_parent(self):
        chore = self.awaiting_chore()

        self.client.post(
            self.changelist_url,
            {"action": "approve_selected", "_selected_action": chore.pk},
        )

        self.assertEqual(stored(chore.events.get(action=APPROVE))["actor_mode"], PARENT)

    def test_reject_selected_records_parent(self):
        chore = self.awaiting_chore()

        self.client.post(
            self.changelist_url,
            {
                "action": "reject_selected",
                "_selected_action": chore.pk,
                "apply": "1",
                "reason": "Do it again",
            },
        )

        event = stored(chore.events.get(action=REJECT))
        self.assertEqual(event["actor_mode"], PARENT)
        self.assertEqual(event["reason"], "Do it again")


# --- Reading the history back --------------------------------------------


class ReadingHistoryTests(ActivityTestCase):
    def test_a_chore_lists_its_events_newest_first_without_re_stating_ordering(self):
        chore = self.make_chore()
        chore.claim(self.ana)
        chore.complete()
        chore.approve()

        self.assertEqual(
            [event.action for event in chore.events.all()],
            [APPROVE, COMPLETE, CLAIM],
        )

    def test_events_in_the_same_microsecond_fall_back_to_the_id(self):
        chore = self.make_chore()
        moment = timezone.now()
        first = ChoreEvent.objects.create(
            chore=chore,
            action=CLAIM,
            from_status=AVAILABLE,
            to_status=CLAIMED,
            actor_mode=SYSTEM,
            acting_child=self.ana,
            reason="",
            occurred_at=moment,
        )
        second = ChoreEvent.objects.create(
            chore=chore,
            action=RELEASE,
            from_status=CLAIMED,
            to_status=AVAILABLE,
            actor_mode=SYSTEM,
            acting_child=None,
            reason="",
            occurred_at=moment,
        )

        self.assertGreater(second.pk, first.pk)
        self.assertEqual(
            [event.pk for event in chore.events.all()], [second.pk, first.pk]
        )

    def test_one_chores_events_never_appear_on_another(self):
        first = self.make_chore(title="Dishes")
        second = self.make_chore(title="Bins")

        first.claim(self.ana)
        second.claim(self.bob)
        first.complete()
        second.release()

        self.assertEqual([e.action for e in first.events.all()], [COMPLETE, CLAIM])
        self.assertEqual([e.action for e in second.events.all()], [RELEASE, CLAIM])
        self.assertEqual(
            set(first.events.values_list("chore_id", flat=True)), {first.pk}
        )
        self.assertEqual(
            set(second.events.values_list("chore_id", flat=True)), {second.pk}
        )


# --- The admin panel -----------------------------------------------------


class AdminActivityPanelTests(ActivityTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            username="parent", email="parent@example.com", password="password"
        )
        self.client.force_login(self.user)

    def change_url(self, chore):
        return reverse("admin:chores_chore_change", args=[chore.pk])

    def add_events(self, chore, count, occurred_at=None):
        base = occurred_at or timezone.now()
        children = (self.ana, self.bob)
        return [
            ChoreEvent.objects.create(
                chore=chore,
                action=CLAIM,
                from_status=AVAILABLE,
                to_status=CLAIMED,
                actor_mode=KID,
                acting_child=children[index % 2],
                reason="",
                occurred_at=base + timedelta(seconds=index),
            )
            for index in range(count)
        ]

    def test_the_change_page_shows_the_chores_events_newest_first(self):
        chore = self.make_chore()
        chore.claim(self.ana, actor_mode=KID)
        chore.complete(actor_mode=KID)
        chore.reject("Sink still full", actor_mode=PARENT)

        response = self.client.get(self.change_url(chore))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity")
        self.assertContains(response, "Sink still full")
        self.assertContains(response, "Ana")
        self.assertLess(body.index(">reject<"), body.index(">complete<"))
        self.assertLess(body.index(">complete<"), body.index(">claim<"))
        for label in (AVAILABLE, CLAIMED, AWAITING_APPROVAL, RETURNED, KID, PARENT):
            self.assertIn(label, body)

    def test_another_chores_events_are_not_shown(self):
        chore = self.claimed_chore()
        other = self.make_chore(title="Bins")
        other.claim(self.bob, actor_mode=KID)

        response = self.client.get(self.change_url(chore))
        shown = response.context["inline_admin_formsets"][0].formset.get_queryset()

        self.assertEqual([event.chore_id for event in shown], [chore.pk])

    def test_the_panel_is_read_only_in_every_direction(self):
        inline = ChoreEventInline(Chore, admin.site)
        request = None

        self.assertFalse(inline.has_add_permission(request))
        self.assertFalse(inline.has_change_permission(request))
        self.assertFalse(inline.has_delete_permission(request))
        self.assertEqual(inline.extra, 0)
        self.assertEqual(inline.max_num, 0)
        self.assertFalse(inline.can_delete)
        self.assertEqual(tuple(inline.readonly_fields), tuple(inline.fields))

    def test_no_empty_extra_rows_are_offered(self):
        chore = self.claimed_chore()

        response = self.client.get(self.change_url(chore))
        formset = response.context["inline_admin_formsets"][0].formset

        self.assertEqual(formset.total_form_count(), formset.initial_form_count())
        self.assertNotContains(response, "events-empty")

    def test_posting_the_chore_change_form_creates_or_alters_no_event(self):
        chore = self.claimed_chore()
        before = {event.pk: stored(event) for event in chore.events.all()}

        response = self.client.post(
            self.change_url(chore),
            {
                "title": "Dishes renamed",
                "notes": "",
                "category": chore.category,
                "priority": chore.priority,
                "status": chore.status,
                "due_at_0": "",
                "due_at_1": "",
                "reward_amount": "",
                "assigned_child": chore.assigned_child_id,
                "recurrence_rule": "",
                "generated_from": "",
                "rejection_reason": "",
                "claimed_at_0": "",
                "claimed_at_1": "",
                "completed_at_0": "",
                "completed_at_1": "",
                "approved_at_0": "",
                "approved_at_1": "",
                "events-TOTAL_FORMS": "1",
                "events-INITIAL_FORMS": "1",
                "events-MIN_NUM_FORMS": "0",
                "events-MAX_NUM_FORMS": "0",
                "claims-TOTAL_FORMS": "1",
                "claims-INITIAL_FORMS": "1",
                "claims-MIN_NUM_FORMS": "0",
                "claims-MAX_NUM_FORMS": "0",
                "claims-0-id": chore.claims.first().pk,
                "claims-0-chore": chore.pk,
                "claims-0-child": chore.claims.first().child_id,
                "claims-0-claimed_at_0": "",
                "claims-0-claimed_at_1": "",
                "claims-0-completed_at_0": "",
                "claims-0-completed_at_1": "",
                "claims-0-reward_share": "",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        chore.refresh_from_db()
        self.assertEqual(chore.title, "Dishes renamed")
        self.assertEqual(
            {event.pk: stored(event) for event in chore.events.all()}, before
        )

    def test_ten_events_cost_the_same_number_of_queries_as_one(self):
        one = self.make_chore(title="One")
        many = self.make_chore(title="Many")
        self.add_events(one, 1)
        self.add_events(many, 10)

        # Warm anything the admin caches on first use (content types,
        # permissions) so the two measurements compare like with like.
        self.client.get(self.change_url(one))

        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(self.client.get(self.change_url(one)).status_code, 200)
        baseline = len(captured)

        with self.assertNumQueries(baseline):
            self.assertEqual(self.client.get(self.change_url(many)).status_code, 200)

        with self.assertNumQueries(baseline):
            self.assertEqual(self.client.get(self.change_url(one)).status_code, 200)

    def test_the_inline_queryset_joins_the_acting_child(self):
        inline = ChoreEventInline(Chore, admin.site)
        request = RequestFactory().get("/")
        request.user = self.user

        self.assertEqual(
            inline.get_queryset(request).query.select_related, {"acting_child": {}}
        )

    def test_an_untouched_chore_says_nothing_has_happened(self):
        chore = self.make_chore()

        response = self.client.get(self.change_url(chore))

        self.assertContains(response, NO_ACTIVITY_YET)
        self.assertNotContains(response, ACTIVITY_PREDATES_LOG)

    def test_a_pre_migration_chore_says_its_history_predates_the_log(self):
        for field, value in (
            ("claimed_at", timezone.now()),
            ("completed_at", timezone.now()),
            ("approved_at", timezone.now()),
            ("rejection_reason", "Not clean"),
        ):
            with self.subTest(field=field):
                chore = self.make_chore(title=f"Legacy {field}", **{field: value})

                response = self.client.get(self.change_url(chore))

                self.assertContains(response, ACTIVITY_PREDATES_LOG)
                self.assertNotContains(response, NO_ACTIVITY_YET)

    def test_a_chore_with_events_shows_neither_empty_message(self):
        chore = self.claimed_chore()

        response = self.client.get(self.change_url(chore))

        self.assertNotContains(response, NO_ACTIVITY_YET)
        self.assertNotContains(response, ACTIVITY_PREDATES_LOG)

    def test_the_add_page_carries_no_activity_panel(self):
        # There is no chore yet to have a history, and the add form must
        # keep working exactly as it did before this panel existed.
        response = self.client.get(reverse("admin:chores_chore_add"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["inline_admin_formsets"], [])

    def test_chore_event_is_not_registered_as_its_own_admin_model(self):
        self.assertNotIn(ChoreEvent, admin.site._registry)
