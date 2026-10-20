from django.contrib import admin

from .models import ChangelogEntry


@admin.register(ChangelogEntry)
class ChangelogEntryAdmin(admin.ModelAdmin):
    list_display = ['version', 'release_date', 'change_type', 'title', 'is_breaking']
    list_filter = ['change_type', 'is_breaking', 'version']
    search_fields = ['title', 'description', 'endpoint']
    ordering = ['-release_date']
