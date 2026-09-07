from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from chores.models import Child, Chore


class FamilyBoardTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.bob = Child.objects.create(name="Bob")
        self.dishes = Chore.objects.create(
            title="Wash dishes",
            category=Chore.Category.KITCHEN,
            priority=Chore.Priority.URGENT,
            status=Chore.Status.CLAIMED,
            assigned_child=self.ana,
            reward_amount=Decimal("2.50"),
        )
        self.laundry = Chore.objects.create(
            title="Fold laundry",
            category=Chore.Category.LAUNDRY,
            priority=Chore.Priority.NORMAL,
            status=Chore.Status.AVAILABLE,
            assigned_child=self.bob,
            reward_amount=None,
        )
        self.shared = Chore.objects.create(
            title="Sweep porch",
            category=Chore.Category.OUTDOORS,
            priority=Chore.Priority.NORMAL,
            status=Chore.Status.AVAILABLE,
            is_shared=True,
        )

    def get_board(self, query=""):
        return self.client.get(reverse("family_board") + query)

    def test_board_lists_all_required_fields_and_unpaid_label(self):
        response = self.get_board()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wash dishes")
        self.assertContains(response, "Kitchen")
        self.assertContains(response, "Urgent")
        self.assertContains(response, "Claimed")
        self.assertContains(response, "Ana")
        self.assertContains(response, "2.50")
        self.assertContains(response, "Fold laundry")
        self.assertContains(response, "Unpaid")
        self.assertContains(response, "Unassigned/shared")
        self.assertNotContains(response, 'name="claim"')
        self.assertNotContains(response, 'name="approve"')

    def test_each_filter_limits_the_rows(self):
        cases = (
            (f"?child={self.ana.pk}", ["Wash dishes"], ["Fold laundry", "Sweep porch"]),
            ("?category=kitchen", ["Wash dishes"], ["Fold laundry", "Sweep porch"]),
            ("?status=available", ["Fold laundry", "Sweep porch"], ["Wash dishes"]),
            ("?priority=urgent", ["Wash dishes"], ["Fold laundry", "Sweep porch"]),
        )
        for query, present, absent in cases:
            with self.subTest(query=query):
                response = self.get_board(query)
                for title in present:
                    self.assertContains(response, title)
                for title in absent:
                    self.assertNotContains(response, title)

    def test_combined_filters_apply_together(self):
        response = self.get_board(
            f"?child={self.ana.pk}&category=kitchen&status=claimed&priority=urgent"
        )

        self.assertContains(response, "Wash dishes")
        self.assertNotContains(response, "Fold laundry")
        self.assertNotContains(response, "Sweep porch")

    def test_empty_and_malformed_filters_are_safe(self):
        empty = self.get_board("?child=999999")
        self.assertEqual(empty.status_code, 200)
        self.assertContains(empty, "No chores match these filters.")

        malformed = self.get_board("?child=not-an-id&category=not-real&status=not-real")
        self.assertEqual(malformed.status_code, 200)
        self.assertContains(malformed, "Wash dishes")
        self.assertContains(malformed, "Fold laundry")
        self.assertContains(malformed, "Sweep porch")

    def test_parent_and_kid_sessions_see_the_same_board(self):
        parent = self.get_board()
        self.client.post(reverse("set_session_mode"), {"mode": "kid"})
        kid = self.get_board()

        for title in ("Wash dishes", "Fold laundry", "Sweep porch"):
            self.assertContains(parent, title)
            self.assertContains(kid, title)
