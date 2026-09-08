from decimal import Decimal

from django.test import TestCase

from chores.balances import annotate_balances, unpaid_balance
from chores.models import Child, Chore, ChoreClaim
from chores.state_machine import InvalidChoreTransition


class ChoreClaimTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.bob = Child.objects.create(name="Bob")
        self.cy = Child.objects.create(name="Cy")

    def shared_chore(self, reward="5.00"):
        return Chore.objects.create(
            title="Sweep porch",
            is_shared=True,
            reward_amount=reward,
        )

    def test_shared_claims_are_ordered_and_keep_the_legacy_pointer_on_the_first(self):
        chore = self.shared_chore()
        chore.claim(self.ana)
        chore.claim(self.bob)

        chore.refresh_from_db()
        self.assertEqual(chore.claimant_ids, [self.ana.pk, self.bob.pk])
        self.assertEqual(chore.assigned_child_id, self.ana.pk)
        self.assertEqual(chore.claimed_at, chore.claims.first().claimed_at)
        self.assertEqual(ChoreClaim.objects.filter(chore=chore).count(), 2)

    def test_complete_without_a_child_is_ambiguous_and_selected_claim_is_stamped(self):
        chore = self.shared_chore()
        chore.claim(self.ana)
        chore.claim(self.bob)

        with self.assertRaisesRegex(InvalidChoreTransition, "several children"):
            chore.complete()

        chore.complete(child=self.bob)
        self.assertEqual(chore.status, Chore.Status.AWAITING_APPROVAL)
        self.assertIsNotNone(chore.claims.get(child=self.bob).completed_at)
        self.assertIsNone(chore.claims.get(child=self.ana).completed_at)

    def test_release_removes_only_the_named_claim_until_the_last_one(self):
        chore = self.shared_chore()
        chore.claim(self.ana)
        chore.claim(self.bob)

        chore.release(child=self.bob)
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.CLAIMED)
        self.assertEqual(chore.claimant_ids, [self.ana.pk])
        self.assertEqual(chore.assigned_child_id, self.ana.pk)

        chore.release(child=self.ana)
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.AVAILABLE)
        self.assertIsNone(chore.assigned_child_id)
        self.assertEqual(chore.claims.count(), 0)

    def test_approval_splits_reward_cents_and_balance_uses_claim_shares(self):
        chore = self.shared_chore()
        for child in (self.ana, self.bob, self.cy):
            chore.claim(child)

        chore.complete(child=self.ana)
        chore.approve()

        self.assertEqual(
            list(chore.claims.values_list("reward_share", flat=True)),
            [Decimal("1.67"), Decimal("1.67"), Decimal("1.66")],
        )
        self.assertEqual(unpaid_balance(self.ana), Decimal("1.67"))
        self.assertEqual(unpaid_balance(self.bob), Decimal("1.67"))
        self.assertEqual(unpaid_balance(self.cy), Decimal("1.66"))
        rows = annotate_balances(Child.objects.order_by("pk"))
        self.assertEqual(
            list(rows.values_list("total_earned", flat=True)),
            [Decimal("1.67"), Decimal("1.67"), Decimal("1.66")],
        )

    def test_reject_clears_completion_for_all_claims_but_keeps_the_rows(self):
        chore = self.shared_chore()
        chore.claim(self.ana)
        chore.claim(self.bob)
        chore.complete(child=self.ana)
        chore.reject("Please redo it")

        self.assertEqual(chore.status, Chore.Status.RETURNED)
        self.assertEqual(chore.claims.count(), 2)
        self.assertFalse(chore.claims.exclude(completed_at=None).exists())

