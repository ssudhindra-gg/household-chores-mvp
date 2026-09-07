"""Plain admin registrations for the core domain models.

Deliberately unconfigured: no custom actions, list filters, search fields or
styling. Approvals, payouts and the rest of the parent workflow get their own
task; this only makes the rows creatable and inspectable by hand.
"""

from django.contrib import admin

from .models import Child, Chore, ChoreRequest, Payout, RecurrenceRule

admin.site.register(Child)
admin.site.register(Chore)
admin.site.register(ChoreRequest)
admin.site.register(Payout)
admin.site.register(RecurrenceRule)
