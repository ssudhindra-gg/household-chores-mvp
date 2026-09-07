"""
URL configuration for household_chores project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path

from chores import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("mode/", views.mode_switch, name="mode_switch"),
    path("mode/set/", views.set_session_mode, name="set_session_mode"),
    path("chores/<int:chore_id>/claim/", views.claim_chore, name="claim_chore"),
    path("chores/<int:chore_id>/complete/", views.complete_chore, name="complete_chore"),
    path("chores/<int:chore_id>/approve/", views.approve_chore, name="approve_chore"),
    path("chores/<int:chore_id>/reject/", views.reject_chore, name="reject_chore"),
    path("payouts/record/", views.record_child_payout, name="record_child_payout"),
    path("chore-requests/", views.request_chore, name="request_chore"),
]
