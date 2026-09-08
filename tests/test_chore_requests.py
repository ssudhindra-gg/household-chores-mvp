from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from chores.models import Child, Chore, ChoreRequest


class ChoreRequestFlowTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.ben = Child.objects.create(name="Ben")
        self.request_url = reverse("chores:request_chore")

    def enter_kid(self, child):
        self.client.post(
            reverse("chores:mode_select"),
            {"mode": "kid", "child": child.pk},
        )

    def test_kid_form_creates_request_for_the_active_child(self):
        self.enter_kid(self.ana)

        response = self.client.post(
            self.request_url,
            {"title": "Wash the car", "notes": "On Saturday", "child_id": self.ben.pk},
        )

        self.assertEqual(response.status_code, 302)
        request = ChoreRequest.objects.get()
        self.assertEqual(request.requested_by_id, self.ana.pk)
        self.assertEqual(request.status, ChoreRequest.Status.PENDING)
        self.assertEqual(request.notes, "On Saturday")
        self.assertTrue(
            any("submitted" in str(message) for message in get_messages(response.wsgi_request))
        )

    def test_blank_title_is_rejected_without_creating_a_request(self):
        self.enter_kid(self.ana)

        response = self.client.post(self.request_url, {"title": "", "notes": "Nope"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        self.assertEqual(ChoreRequest.objects.count(), 0)

    def test_parent_mode_cannot_submit_a_request(self):
        response = self.client.post(self.request_url, {"title": "Clean windows"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ChoreRequest.objects.count(), 0)
        self.assertContains(self.client.get(response["Location"]), "Switch to Kid Mode")


class ChoreRequestAdminTests(TestCase):
    def setUp(self):
        self.child = Child.objects.create(name="Ana")
        self.user = get_user_model().objects.create_superuser(
            username="parent", email="parent@example.com", password="password"
        )
        self.client.force_login(self.user)
        self.url = reverse("admin:chores_chorerequest_changelist")

    def make_request(self, title="Take out bins"):
        return ChoreRequest.objects.create(requested_by=self.child, title=title)

    def test_accept_creates_and_links_one_available_chore(self):
        request = self.make_request()

        response = self.client.post(
            self.url,
            {"action": "accept_selected", "_selected_action": request.pk},
        )

        self.assertEqual(response.status_code, 302)
        request.refresh_from_db()
        self.assertEqual(request.status, ChoreRequest.Status.ACCEPTED)
        self.assertIsNotNone(request.resulting_chore_id)
        chore = request.resulting_chore
        self.assertEqual(chore.title, request.title)
        self.assertEqual(chore.notes, request.notes)
        self.assertEqual(chore.assigned_child_id, self.child.pk)
        self.assertEqual(chore.status, Chore.Status.AVAILABLE)

    def test_decline_marks_request_without_creating_a_chore(self):
        request = self.make_request()

        response = self.client.post(
            self.url,
            {"action": "decline_selected", "_selected_action": request.pk},
        )

        self.assertEqual(response.status_code, 302)
        request.refresh_from_db()
        self.assertEqual(request.status, ChoreRequest.Status.DECLINED)
        self.assertIsNone(request.resulting_chore_id)
        self.assertEqual(Chore.objects.count(), 0)

    def test_reprocessing_an_accepted_request_does_not_duplicate_a_chore(self):
        request = self.make_request()
        self.client.post(
            self.url,
            {"action": "accept_selected", "_selected_action": request.pk},
        )
        resulting_id = Chore.objects.get().pk

        response = self.client.post(
            self.url,
            {"action": "accept_selected", "_selected_action": request.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Chore.objects.count(), 1)
        self.assertEqual(Chore.objects.get().pk, resulting_id)
        self.assertTrue(
            any("already accepted" in str(message) for message in get_messages(response.wsgi_request))
        )
