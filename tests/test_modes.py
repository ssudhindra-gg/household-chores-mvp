from django.contrib import admin
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import include, path, reverse
from django.views import View

from chores.modes import (
    ACTING_CHILD_SESSION_KEY,
    DEFAULT_MODE,
    MODE_SESSION_KEY,
    KidModeRequiredMixin,
    Mode,
    ParentModeRequiredMixin,
    current_child,
    kid_mode_required,
    parent_mode_required,
)
from chores.models import Child


@parent_mode_required
def parent_probe(request):
    return HttpResponse("parent probe")


@kid_mode_required
def kid_probe(request):
    return HttpResponse(f"kid probe: {request.acting_child.name}")


class ParentProbeView(ParentModeRequiredMixin, View):
    def get(self, request):
        return HttpResponse("parent cbv")


class KidProbeView(KidModeRequiredMixin, View):
    def get(self, request):
        return HttpResponse(f"kid cbv: {request.acting_child.name}")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("chores.urls")),
    path("test-probe/parent/", parent_probe, name="parent_probe"),
    path("test-probe/kid/", kid_probe, name="kid_probe"),
    path("test-probe/parent-cbv/", ParentProbeView.as_view(), name="parent_probe_cbv"),
    path("test-probe/kid-cbv/", KidProbeView.as_view(), name="kid_probe_cbv"),
]


@override_settings(ROOT_URLCONF="tests.test_modes")
class ModeSelectionTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.zoe = Child.objects.create(name="Zoe")
        self.url = reverse("chores:mode_select")

    def test_fresh_get_defaults_to_parent_lists_children_and_varies_on_cookie(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Parent Mode")
        self.assertContains(response, "Kid Mode: Ana")
        self.assertContains(response, "Kid Mode: Zoe")
        self.assertNotIn("sessionid", response.cookies)
        self.assertIn("Cookie", response.headers["Vary"])
        self.assertEqual(dict(self.client.session.items()), {})

    def test_zero_children_is_a_usable_parent_page(self):
        Child.objects.all().delete()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No children exist yet")

    def test_junk_mode_self_heals_to_the_default(self):
        for junk in ("PARENT", "", 0, "admin"):
            with self.subTest(junk=junk):
                session = self.client.session
                session[MODE_SESSION_KEY] = junk
                session.save()
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(MODE_SESSION_KEY, self.client.session)
                self.assertEqual(response.context["current_mode"], DEFAULT_MODE)

    def test_kid_requires_a_child_and_invalid_choice_preserves_session(self):
        before = dict(self.client.session.items())
        response = self.client.post(self.url, {"mode": "kid"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a child")
        self.assertEqual(dict(self.client.session.items()), before)

        response = self.client.post(self.url, {"mode": "kid", "child": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dict(self.client.session.items()), before)

        response = self.client.post(self.url, {"mode": "kid", "child": 99999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dict(self.client.session.items()), before)

    def test_kid_mode_stores_int_child_and_parent_keeps_memory(self):
        response = self.client.post(self.url, {"mode": "kid", "child": self.ana.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session[MODE_SESSION_KEY], Mode.KID.value)
        self.assertIsInstance(self.client.session[ACTING_CHILD_SESSION_KEY], int)
        self.assertEqual(self.client.get(self.url).context["current_child"], self.ana)

        self.client.post(self.url, {"mode": "parent"})
        self.assertEqual(self.client.session[MODE_SESSION_KEY], Mode.PARENT.value)
        self.assertEqual(self.client.session[ACTING_CHILD_SESSION_KEY], self.ana.pk)
        self.assertIsNone(self.client.get(self.url).context["current_child"])

        response = self.client.post(self.url, {"mode": "kid"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(self.url).context["current_child"], self.ana)

    def test_invalid_mode_is_a_bad_request(self):
        for data in ({}, {"mode": "sideways"}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(self.url, data).status_code, 400)

    def test_next_is_validated_and_post_redirect_get_is_used(self):
        response = self.client.post(self.url, {"mode": "parent", "next": "/board/"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/board/")
        for next_url in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
            response = self.client.post(self.url, {"mode": "parent", "next": next_url})
            self.assertEqual(response["Location"], self.url)

    def test_only_get_and_post_are_allowed(self):
        for method in ("put", "delete", "patch"):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(self.url).status_code, 405)

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(self.url, {"mode": "parent"})
        self.assertEqual(response.status_code, 403)


@override_settings(ROOT_URLCONF="tests.test_modes")
class ModeResolverAndGuardTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.url = reverse("chores:mode_select")

    def enter_kid(self):
        self.client.post(self.url, {"mode": "kid", "child": self.ana.pk})

    def test_resolvers_do_not_query_in_parent_and_child_is_cached_in_kid(self):
        request = RequestFactory().get("/")
        from django.contrib.sessions.middleware import SessionMiddleware

        SessionMiddleware(lambda _request: HttpResponse()).process_request(request)
        request.session[MODE_SESSION_KEY] = Mode.PARENT.value
        request.session[ACTING_CHILD_SESSION_KEY] = self.ana.pk
        with self.assertNumQueries(0):
            self.assertIsNone(current_child(request))
        request.session[MODE_SESSION_KEY] = Mode.KID.value
        del request._chores_current_child
        with self.assertNumQueries(1):
            self.assertEqual(current_child(request), self.ana)
            self.assertEqual(current_child(request), self.ana)

    def test_function_and_cbv_guards_work_in_both_modes(self):
        self.assertEqual(self.client.get(reverse("parent_probe")).status_code, 200)
        self.assertEqual(self.client.get(reverse("parent_probe_cbv")).status_code, 200)
        self.assertEqual(self.client.get(reverse("kid_probe")).status_code, 302)
        self.assertEqual(self.client.get(reverse("kid_probe_cbv")).status_code, 302)
        self.enter_kid()
        self.assertEqual(self.client.get(reverse("kid_probe")).content.decode(), "kid probe: Ana")
        self.assertEqual(self.client.get(reverse("kid_probe_cbv")).content.decode(), "kid cbv: Ana")
        self.assertEqual(self.client.get(reverse("parent_probe")).status_code, 302)
        self.assertEqual(self.client.get(reverse("parent_probe_cbv")).status_code, 302)

    def test_refused_normal_request_redirects_with_warning_and_safe_referer(self):
        self.enter_kid()
        response = self.client.get(reverse("parent_probe"), HTTP_REFERER="/board/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/board/")
        followed = self.client.get(response["Location"])
        self.assertContains(followed, "Switch to Parent Mode to continue")

        response = self.client.get(
            reverse("parent_probe"), HTTP_REFERER="https://evil.example/"
        )
        self.assertEqual(response["Location"], self.url)

    def test_refused_htmx_request_returns_only_the_message_partial(self):
        self.enter_kid()
        response = self.client.get(reverse("parent_probe"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Switch to Parent Mode to continue")
        self.assertNotContains(response, "<html")
        self.assertEqual(response["HX-Retarget"], "#messages")
        self.assertEqual(response["HX-Reswap"], "innerHTML")
        self.assertNotIn("Location", response)

    def test_stale_child_is_dropped_and_kid_guard_refuses(self):
        self.enter_kid()
        self.ana.delete()
        response = self.client.get(reverse("kid_probe"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(ACTING_CHILD_SESSION_KEY, self.client.session)

    def test_admin_is_independent_of_mode(self):
        User = get_user_model()
        user = User.objects.create_superuser(
            username="parent", email="parent@example.com", password="password"
        )
        self.client.force_login(user)
        self.enter_kid()
        with override_settings(ROOT_URLCONF="household_chores.urls"):
            self.assertEqual(
                self.client.get(reverse("admin:chores_chore_changelist")).status_code,
                200,
            )

    def test_decorators_preserve_metadata_and_kid_sets_acting_child(self):
        self.assertEqual(parent_probe.__name__, "parent_probe")
        self.assertEqual(kid_probe.__name__, "kid_probe")
        self.enter_kid()
        self.assertContains(self.client.get(reverse("kid_probe")), "Ana")
