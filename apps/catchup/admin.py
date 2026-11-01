from django.contrib import admin

from .models import CatchUpRun


@admin.register(CatchUpRun)
class CatchUpRunAdmin(admin.ModelAdmin):
    """Read-only: these rows are evidence about what an outage cost."""

    list_display = ('series', 'triggered_by', 'outcome', 'cause', 'gap_start',
                    'gap_end', 'units_remaining', 'estimated_calls', 'started_at')
    list_filter = ('series', 'outcome', 'triggered_by', 'cause')
    search_fields = ('series', 'detail')
    date_hierarchy = 'started_at'
    readonly_fields = [field.name for field in CatchUpRun._meta.fields]
    list_per_page = 100

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
