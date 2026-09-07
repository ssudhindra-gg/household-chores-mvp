"""Proves the toolchain works end to end before any domain code exists.

Written as a Django ``SimpleTestCase`` so both ``uv run pytest`` and
``uv run python manage.py test`` pick it up.
"""

from django.apps import apps
from django.test import SimpleTestCase


class ChoresAppTests(SimpleTestCase):
    def test_chores_app_is_installed(self):
        self.assertEqual(apps.get_app_config("chores").name, "chores")
