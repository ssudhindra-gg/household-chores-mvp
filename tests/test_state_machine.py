"""Tests for the chore status state machine (issue #3).

Scope: which ``(action, status)`` pairs are legal, what each legal transition
writes, what every illegal one raises, and the status/timestamp invariants
that hold afterwards. Money arithmetic belongs to a later issue and is
asserted *absent* here, not tested.

Rows are re-fetched from the database before asserting, so these tests prove
the transition was saved rather than only mutated in memory.
"""

import ast
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from chores import state_machine
from chores.models import Child, Chore
from chores.state_machine import (
    ACTION_TARGETS,
    APPROVE,
    APPROVED,
    AVAILABLE,
    AWAITING_APPROVAL,
    CLAIM,
    CLAIMED,
    COMPLETE,
    REJECT,
    RELEASE,
    RETURNED,
    TRANSITIONS,
    InvalidChoreTransition,
    RejectionReasonRequired,
)

APP_DIR = Path(state_machine.__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

# Every field a transition is allowed to touch, for "nothing changed" checks.
TRACKED_FIELDS = (
    "status",
    "assigned_child_id",
    "claimed_at",
    "completed_at",
    "approved_at",
    "rejection_reason",
)


def snapshot(chore):
    """The transition-relevant columns of ``chore``, re-read from the DB."""
    fresh = Chore.objects.get(pk=chore.pk)
    return {field: getattr(fresh, field) for field in TRACKED_FIELDS}


class TransitionTableTests(SimpleTestCase):
    """The table itself: shape, isolation from the models module."""

    def test_state_machine_imports_without_pulling_in_the_models_module(self):
        # Proves there is no import cycle: the table is keyed by plain
        # strings, so it stands on its own.
        code = (
            "import sys\n"
            "import chores.state_machine\n"
            "assert 'chores.models' not in sys.modules, "
            "'chores.models was imported'\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_module_source_never_imports_chores_models(self):
        tree = ast.parse((APP_DIR / "state_machine.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("models", (node.module or ""))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("models", alias.name)

    def test_table_statuses_match_the_chore_status_choices(self):
        # Guards against the table and the TextChoices set drifting apart.
        self.assertEqual(
            state_machine.table_statuses(), {c.value for c in Chore.Status}
        )

    def test_table_holds_exactly_the_seven_legal_pairs(self):
        pairs = {
            (action, from_status)
            for action, moves in TRANSITIONS.items()
            for from_status in moves
        }
        self.assertEqual(
            pairs,
            {
                (CLAIM, AVAILABLE),
                (CLAIM, RETURNED),
                (COMPLETE, CLAIMED),
                (APPROVE, AWAITING_APPROVAL),
                (REJECT, AWAITING_APPROVAL),
                (RELEASE, CLAIMED),
                (RELEASE, RETURNED),
            },
        )

    def test_approved_is_terminal(self):
        for action, moves in TRANSITIONS.items():
            with self.subTest(action=action):
                self.assertNotIn(APPROVED, moves)

    def test_no_action_is_a_self_transition(self):
        for action, moves in TRANSITIONS.items():
            for from_status, to_status in moves.items():
                with self.subTest(action=action, status=from_status):
                    self.assertNotEqual(from_status, to_status)

    def test_each_action_has_exactly_one_target_status(self):
        for action, moves in TRANSITIONS.items():
            with self.subTest(action=action):
                self.assertEqual(len(set(moves.values())), 1)


class ModuleHygieneTests(SimpleTestCase):
    """Constraints the issue puts on the code itself, not its behaviour."""

    def test_no_naive_datetime_now_in_the_state_machine(self):
        source = (APP_DIR / "state_machine.py").read_text(encoding="utf-8")
        self.assertNotIn("datetime.now", source)
        self.assertIn("timezone.now()", source)

    def test_state_machine_contains_no_money_arithmetic(self):
        source = (APP_DIR / "state_machine.py").read_text(encoding="utf-8")
        for term in ("reward_amount", "balance", "Decimal", "payout"):
            with self.subTest(term=term):
                self.assertNotIn(term, source)

    def test_only_the_state_machine_assigns_chore_status(self):
        # Everywhere else in the app package, ``status`` is only ever read.
        # (The field default in models.py is the one other way it is set.)
        offenders = []
        for path in sorted(APP_DIR.rglob("*.py")):
            if path.name == "state_machine.py" or "migrations" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    targets = [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "status":
                        offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_models_does_not_restate_any_transition_rule(self):
        source = (APP_DIR / "models.py").read_text(encoding="utf-8")
        for status in ("claimed", "awaiting_approval", "returned"):
            # Only the TextChoices definitions may name the stored values.
            self.assertEqual(source.count(f'"{status}"'), 1, status)


class ChoreTransitionTestCase(TestCase):
    """Shared fixtures for the behavioural tests."""

    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.bob = Child.objects.create(name="Bob")

    def make_chore(self, **kwargs):
        kwargs.setdefault("title", "Dishes")
        kwargs.setdefault("reward_amount", Decimal("2.50"))
        return Chore.objects.create(**kwargs)

    def claimed_chore(self, child=None, **kwargs):
        chore = self.make_chore(**kwargs)
        chore.claim(child or self.ana)
        return chore

    def awaiting_chore(self, child=None, **kwargs):
        chore = self.claimed_chore(child, **kwargs)
        chore.complete()
        return chore

    def approved_chore(self, child=None, **kwargs):
        chore = self.awaiting_chore(child, **kwargs)
        chore.approve()
        return chore

    def returned_chore(self, child=None, reason="Missed the pans", **kwargs):
        chore = self.awaiting_chore(child, **kwargs)
        chore.reject(reason)
        return chore

    def chore_in(self, status, **kwargs):
        """A chore parked in ``status`` by the legal route that reaches it."""
        return {
            AVAILABLE: self.make_chore,
            CLAIMED: self.claimed_chore,
            AWAITING_APPROVAL: self.awaiting_chore,
            APPROVED: self.approved_chore,
            RETURNED: self.returned_chore,
        }[status](**kwargs)

    def assertInvariants(self, chore):
        """The status/timestamp table every row must satisfy."""
        fresh = Chore.objects.get(pk=chore.pk)
        if fresh.status == AVAILABLE:
            self.assertIsNone(fresh.claimed_at)
            self.assertIsNone(fresh.completed_at)
            self.assertIsNone(fresh.approved_at)
        elif fresh.status == CLAIMED:
            self.assertIsNotNone(fresh.claimed_at)
            self.assertIsNone(fresh.completed_at)
            self.assertIsNone(fresh.approved_at)
            self.assertIsNotNone(fresh.assigned_child_id)
        elif fresh.status == AWAITING_APPROVAL:
            self.assertIsNotNone(fresh.claimed_at)
            self.assertIsNotNone(fresh.completed_at)
            self.assertIsNone(fresh.approved_at)
        elif fresh.status == APPROVED:
            self.assertIsNotNone(fresh.claimed_at)
            self.assertIsNotNone(fresh.completed_at)
            self.assertIsNotNone(fresh.approved_at)
        elif fresh.status == RETURNED:
            self.assertIsNotNone(fresh.claimed_at)
            self.assertIsNone(fresh.completed_at)
            self.assertIsNone(fresh.approved_at)
            self.assertNotEqual(fresh.rejection_reason, "")
        else:  # pragma: no cover - a status the table does not know
            self.fail(f"unknown status {fresh.status}")


class LegalTransitionTests(ChoreTransitionTestCase):
    """One test per legal pair, asserted on the re-fetched row."""

    def test_claim_from_available(self):
        chore = self.make_chore()
        before = timezone.now()
        self.assertIsNone(chore.claim(self.ana))

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.status, CLAIMED)
        self.assertEqual(fresh.assigned_child_id, self.ana.pk)
        self.assertGreaterEqual(fresh.claimed_at, before)
        self.assertIsNone(fresh.completed_at)
        self.assertIsNone(fresh.approved_at)
        self.assertInvariants(chore)

    def test_claim_from_returned(self):
        chore = self.returned_chore()
        self.assertIsNone(chore.claim(self.ana))

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.status, CLAIMED)
        self.assertEqual(fresh.assigned_child_id, self.ana.pk)
        self.assertIsNotNone(fresh.claimed_at)
        self.assertInvariants(chore)

    def test_complete_from_claimed(self):
        chore = self.claimed_chore()
        before = timezone.now()
        self.assertIsNone(chore.complete())

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.status, AWAITING_APPROVAL)
        self.assertGreaterEqual(fresh.completed_at, before)
        self.assertIsNone(fresh.approved_at)
        self.assertInvariants(chore)

    def test_approve_from_awaiting_approval(self):
        chore = self.awaiting_chore()
        before = timezone.now()
        self.assertIsNone(chore.approve())

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.status, APPROVED)
        self.assertGreaterEqual(fresh.approved_at, before)
        self.assertInvariants(chore)

    def test_reject_from_awaiting_approval(self):
        chore = self.awaiting_chore()
        self.assertIsNone(chore.reject("Sink still dirty"))

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.status, RETURNED)
        self.assertEqual(fresh.rejection_reason, "Sink still dirty")
        self.assertIsNone(fresh.completed_at)
        self.assertIsNotNone(fresh.claimed_at)
        self.assertEqual(fresh.assigned_child_id, self.ana.pk)
        self.assertInvariants(chore)

    def test_release_from_claimed(self):
        chore = self.claimed_chore()
        self.assertIsNone(chore.release())

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.status, AVAILABLE)
        self.assertIsNone(fresh.assigned_child_id)
        self.assertIsNone(fresh.claimed_at)
        self.assertInvariants(chore)

    def test_release_from_returned(self):
        chore = self.returned_chore()
        self.assertIsNone(chore.release())

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.status, AVAILABLE)
        self.assertIsNone(fresh.assigned_child_id)
        self.assertIsNone(fresh.claimed_at)
        # Releasing does not erase why it came back.
        self.assertEqual(fresh.rejection_reason, "Missed the pans")
        self.assertInvariants(chore)

    def test_transitions_are_saved_without_calling_save(self):
        chore = self.make_chore()
        chore.claim(self.ana)
        self.assertEqual(Chore.objects.get(pk=chore.pk).status, CLAIMED)

    def test_save_only_writes_the_fields_the_transition_changed(self):
        chore = self.make_chore()
        chore.title = "Never persisted"  # unrelated in-memory edit
        chore.claim(self.ana)

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.title, "Dishes")
        self.assertEqual(fresh.status, CLAIMED)

    def test_updated_at_is_refreshed_by_a_transition(self):
        chore = self.make_chore()
        original = Chore.objects.get(pk=chore.pk).updated_at
        chore.claim(self.ana)
        self.assertGreater(Chore.objects.get(pk=chore.pk).updated_at, original)


class HappyPathTests(ChoreTransitionTestCase):
    def test_full_happy_path_on_one_chore(self):
        chore = self.make_chore()
        self.assertEqual(chore.status, AVAILABLE)
        self.assertInvariants(chore)

        chore.claim(self.ana)
        self.assertEqual(chore.status, CLAIMED)
        self.assertInvariants(chore)

        chore.complete()
        self.assertEqual(chore.status, AWAITING_APPROVAL)
        self.assertInvariants(chore)

        chore.approve()
        self.assertInvariants(chore)
        self.assertEqual(Chore.objects.get(pk=chore.pk).status, APPROVED)

    def test_rejection_loop_ends_approved_with_the_reason_still_readable(self):
        chore = self.make_chore()
        chore.claim(self.ana)
        chore.complete()
        chore.reject("Floor was skipped")
        self.assertEqual(chore.status, RETURNED)
        self.assertInvariants(chore)

        chore.claim(self.ana)
        self.assertInvariants(chore)
        chore.complete()
        self.assertInvariants(chore)
        chore.approve()

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.status, APPROVED)
        self.assertEqual(fresh.rejection_reason, "Floor was skipped")
        self.assertInvariants(chore)

    def test_unpaid_chore_walks_the_happy_path_unchanged(self):
        chore = self.make_chore(reward_amount=None)
        chore.claim(self.ana)
        chore.complete()
        chore.approve()

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.status, APPROVED)
        self.assertIsNone(fresh.reward_amount)
        self.assertInvariants(chore)

    def test_approve_leaves_the_reward_amount_alone(self):
        chore = self.awaiting_chore(reward_amount=Decimal("3.25"))
        chore.approve()
        self.assertEqual(
            Chore.objects.get(pk=chore.pk).reward_amount, Decimal("3.25")
        )


class IllegalTransitionTests(ChoreTransitionTestCase):
    """The 18 pairs the table does not contain, plus what they leave behind."""

    def attempt(self, chore, action):
        """Call ``action`` on ``chore`` with otherwise-valid arguments."""
        if action == CLAIM:
            chore.claim(self.ana)
        elif action == COMPLETE:
            chore.complete()
        elif action == APPROVE:
            chore.approve()
        elif action == REJECT:
            chore.reject("A perfectly good reason")
        elif action == RELEASE:
            chore.release()
        else:  # pragma: no cover
            self.fail(f"unknown action {action}")

    def illegal_pairs(self):
        return [
            (action, status)
            for action in TRANSITIONS
            for status in (c.value for c in Chore.Status)
            if status not in TRANSITIONS[action]
        ]

    def test_there_are_eighteen_illegal_pairs(self):
        self.assertEqual(len(self.illegal_pairs()), 18)
        self.assertEqual(len(TRANSITIONS) * len(Chore.Status), 25)

    def test_every_illegal_pair_raises_and_changes_nothing(self):
        for action, status in self.illegal_pairs():
            with self.subTest(action=action, status=status):
                chore = self.chore_in(status)
                before = snapshot(chore)
                with self.assertRaises(InvalidChoreTransition) as ctx:
                    self.attempt(chore, action)

                self.assertEqual(snapshot(chore), before)
                error = ctx.exception
                self.assertEqual(error.chore, chore)
                self.assertEqual(error.from_status, status)
                self.assertEqual(error.to_status, ACTION_TARGETS[action])
                # str() names the action, the current status and the status
                # the action was expecting to find.
                self.assertIn(action, str(error))
                self.assertIn(status, str(error))
                for expected in TRANSITIONS[action]:
                    self.assertIn(expected, str(error))

    def test_exception_hierarchy_and_attributes(self):
        chore = self.make_chore()
        with self.assertRaises(InvalidChoreTransition) as ctx:
            chore.approve()

        error = ctx.exception
        self.assertIsInstance(error, Exception)
        self.assertNotIsInstance(error, (ValueError, AssertionError))
        self.assertEqual(error.chore, chore)
        self.assertEqual(error.from_status, AVAILABLE)
        self.assertEqual(error.to_status, APPROVED)
        self.assertEqual(
            str(error),
            "Cannot approve a chore that is available (expected awaiting_approval)",
        )

    def test_rejection_reason_required_is_catchable_as_the_base_class(self):
        self.assertTrue(issubclass(RejectionReasonRequired, InvalidChoreTransition))
        chore = self.awaiting_chore()
        with self.assertRaises(InvalidChoreTransition):
            chore.reject("")

    def test_transitions_are_not_idempotent(self):
        chore = self.claimed_chore()
        with self.assertRaises(InvalidChoreTransition):
            chore.claim(self.ana)

        chore.complete()
        with self.assertRaises(InvalidChoreTransition):
            chore.complete()

    def test_approving_twice_raises_and_keeps_the_original_approved_at(self):
        chore = self.approved_chore()
        original = Chore.objects.get(pk=chore.pk).approved_at

        with self.assertRaises(InvalidChoreTransition):
            chore.approve()

        self.assertEqual(Chore.objects.get(pk=chore.pk).approved_at, original)

    def test_complete_requires_an_assigned_child_even_when_claimed(self):
        chore = self.claimed_chore()
        # Simulate a row hand-edited around the state machine.
        Chore.objects.filter(pk=chore.pk).update(assigned_child=None)
        chore = Chore.objects.get(pk=chore.pk)
        before = snapshot(chore)

        with self.assertRaises(InvalidChoreTransition):
            chore.complete()
        self.assertEqual(snapshot(chore), before)

    def test_approve_requires_an_assigned_child(self):
        chore = self.awaiting_chore()
        Chore.objects.filter(pk=chore.pk).update(assigned_child=None)
        chore = Chore.objects.get(pk=chore.pk)
        before = snapshot(chore)

        with self.assertRaises(InvalidChoreTransition):
            chore.approve()
        self.assertEqual(snapshot(chore), before)


class RejectionReasonTests(ChoreTransitionTestCase):
    def test_blank_reasons_raise_and_leave_the_chore_awaiting_approval(self):
        for reason in ("", None, "   ", "\t\n"):
            with self.subTest(reason=repr(reason)):
                chore = self.returned_chore(reason="Original reason")
                chore.claim(self.ana)
                chore.complete()
                before = snapshot(chore)

                with self.assertRaises(RejectionReasonRequired):
                    chore.reject(reason)

                after = snapshot(chore)
                self.assertEqual(after, before)
                self.assertEqual(after["status"], AWAITING_APPROVAL)
                self.assertEqual(after["rejection_reason"], "Original reason")

    def test_reason_is_stored_stripped(self):
        chore = self.awaiting_chore()
        chore.reject("  Bins not taken out \n")
        self.assertEqual(
            Chore.objects.get(pk=chore.pk).rejection_reason, "Bins not taken out"
        )

    def test_rejecting_again_overwrites_the_previous_reason(self):
        chore = self.returned_chore(reason="First time")
        chore.claim(self.ana)
        chore.complete()
        chore.reject("Second time")
        self.assertEqual(Chore.objects.get(pk=chore.pk).rejection_reason, "Second time")

    def test_no_other_transition_clears_the_rejection_reason(self):
        for finish in ("approve", "release", "claim"):
            with self.subTest(finish=finish):
                chore = self.returned_chore(reason="Why it came back")
                if finish == "release":
                    chore.release()
                else:
                    chore.claim(self.ana)
                    if finish == "approve":
                        chore.complete()
                        chore.approve()
                self.assertEqual(
                    Chore.objects.get(pk=chore.pk).rejection_reason,
                    "Why it came back",
                )


class ClaimEligibilityTests(ChoreTransitionTestCase):
    def test_claiming_with_none_raises_and_changes_nothing(self):
        chore = self.make_chore()
        before = snapshot(chore)
        with self.assertRaises(InvalidChoreTransition):
            chore.claim(None)
        self.assertEqual(snapshot(chore), before)

    def test_unassigned_chore_can_be_claimed_by_any_child(self):
        for child in (self.ana, self.bob):
            with self.subTest(child=child.name):
                chore = self.make_chore()
                chore.claim(child)
                self.assertEqual(
                    Chore.objects.get(pk=chore.pk).assigned_child_id, child.pk
                )

    def test_shared_chore_can_be_claimed_by_a_child_other_than_assigned(self):
        chore = self.make_chore(is_shared=True, assigned_child=self.ana)
        chore.claim(self.bob)

        fresh = Chore.objects.get(pk=chore.pk)
        self.assertEqual(fresh.status, CLAIMED)
        self.assertEqual(fresh.assigned_child_id, self.bob.pk)

    def test_preassigned_chore_is_claimable_only_by_that_child(self):
        chore = self.make_chore(assigned_child=self.ana)
        before = snapshot(chore)

        with self.assertRaises(InvalidChoreTransition) as ctx:
            chore.claim(self.bob)

        self.assertEqual(snapshot(chore), before)
        self.assertIn("Bob", str(ctx.exception))
        self.assertIn("Ana", str(ctx.exception))

        chore.claim(self.ana)
        self.assertEqual(Chore.objects.get(pk=chore.pk).status, CLAIMED)

    def test_second_child_cannot_claim_an_already_claimed_chore(self):
        chore = self.claimed_chore(self.ana, is_shared=True)
        before = snapshot(chore)
        with self.assertRaises(InvalidChoreTransition):
            chore.claim(self.bob)
        self.assertEqual(snapshot(chore), before)

    def test_reclaiming_after_a_return_overwrites_claimed_at(self):
        chore = self.returned_chore()
        first = Chore.objects.get(pk=chore.pk).claimed_at
        chore.claim(self.ana)
        self.assertGreater(Chore.objects.get(pk=chore.pk).claimed_at, first)


class AllowedActionsTests(ChoreTransitionTestCase):
    def test_actions_per_status(self):
        expected = {
            AVAILABLE: {CLAIM},
            CLAIMED: {COMPLETE, RELEASE},
            AWAITING_APPROVAL: {APPROVE, REJECT},
            APPROVED: set(),
            RETURNED: {CLAIM, RELEASE},
        }
        for status, actions in expected.items():
            with self.subTest(status=status):
                self.assertEqual(set(self.chore_in(status).allowed_actions()), actions)

    def test_approved_chore_has_no_allowed_actions(self):
        self.assertEqual(self.approved_chore().allowed_actions(), [])

    def test_claim_is_excluded_for_a_child_who_may_not_claim(self):
        chore = self.make_chore(assigned_child=self.ana)
        self.assertEqual(chore.allowed_actions(self.ana), [CLAIM])
        self.assertEqual(chore.allowed_actions(self.bob), [])
        # With no child, eligibility is not considered.
        self.assertEqual(chore.allowed_actions(), [CLAIM])

    def test_shared_chore_offers_claim_to_anyone(self):
        chore = self.make_chore(is_shared=True, assigned_child=self.ana)
        self.assertEqual(chore.allowed_actions(self.bob), [CLAIM])

    def test_child_argument_does_not_add_actions(self):
        chore = self.claimed_chore()
        self.assertEqual(
            set(chore.allowed_actions(self.bob)), {COMPLETE, RELEASE}
        )
