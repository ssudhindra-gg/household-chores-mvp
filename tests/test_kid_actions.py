from django.test import TestCase
from django.urls import reverse

from chores.models import Child, Chore


class KidActionTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.bob = Child.objects.create(name="Bob")
        self.mode_url = reverse("chores:mode_select")

    def enter_kid(self, child=None):
        self.client.post(
            self.mode_url,
            {"mode": "kid", "child": (child or self.ana).pk},
        )

    def action_url(self, name, chore):
        return reverse(f"chores:{name}", args=[chore.pk])

    def test_kid_mode_claims_available_chore_with_htmx_fragment(self):
        self.enter_kid()
        chore = Chore.objects.create(title="Dishes")

        response = self.client.post(
            self.action_url("claim_chore", chore),
            {"next": reverse("chores:family_board")},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Location", response)
        self.assertContains(response, 'id="chore-{}"'.format(chore.pk))
        self.assertContains(response, "Mark complete")
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.CLAIMED)
        self.assertEqual(chore.assigned_child_id, self.ana.pk)

    def test_shared_chore_can_be_claimed_by_the_acting_child(self):
        self.enter_kid()
        chore = Chore.objects.create(
            title="Sweep porch", assigned_child=self.bob, is_shared=True
        )

        response = self.client.post(
            self.action_url("claim_chore", chore), {}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        chore.refresh_from_db()
        self.assertEqual(chore.assigned_child_id, self.ana.pk)
        self.assertEqual(chore.status, Chore.Status.CLAIMED)

    def test_preassigned_chore_cannot_be_claimed_by_a_different_child(self):
        self.enter_kid()
        chore = Chore.objects.create(title="Laundry", assigned_child=self.bob)

        response = self.client.post(
            self.action_url("claim_chore", chore), {}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot claim")
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.AVAILABLE)
        self.assertEqual(chore.assigned_child_id, self.bob.pk)

    def test_complete_returns_updated_fragment_for_the_acting_child(self):
        self.enter_kid()
        chore = Chore.objects.create(title="Bins")
        chore.claim(self.ana)

        response = self.client.post(
            self.action_url("complete_chore", chore), {}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Awaiting approval")
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.AWAITING_APPROVAL)

    def test_parent_mode_is_redirected_with_a_message_and_does_not_mutate(self):
        chore = Chore.objects.create(title="Dishes")

        response = self.client.post(self.action_url("claim_chore", chore))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.mode_url)
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.AVAILABLE)
        self.assertContains(self.client.get(response["Location"]), "Switch to Kid Mode")

    def test_invalid_state_returns_htmx_error_without_mutation(self):
        self.enter_kid()
        chore = Chore.objects.create(title="Already done", status=Chore.Status.APPROVED)

        response = self.client.post(
            self.action_url("complete_chore", chore), {}, HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Retarget"], "#messages")
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.APPROVED)

    def test_board_only_exposes_controls_in_kid_mode(self):
        chore = Chore.objects.create(title="Dishes")
        parent = self.client.get(reverse("chores:family_board"))
        self.assertNotContains(parent, 'hx-post="/chores/{}/claim/"'.format(chore.pk))
        self.enter_kid()
        kid = self.client.get(reverse("chores:family_board"))
        self.assertContains(kid, "Claim")
        self.assertContains(kid, "hx-post=\"/chores/{}/claim/\"".format(chore.pk))

    def test_non_post_action_is_rejected(self):
        chore = Chore.objects.create(title="Dishes")
        self.enter_kid()
        self.assertEqual(self.client.get(self.action_url("claim_chore", chore)).status_code, 405)
