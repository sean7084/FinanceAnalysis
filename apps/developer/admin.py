from django.contrib import admin

from .models import ChangelogEntry, DeveloperAPIKey


@admin.register(DeveloperAPIKey)
class DeveloperAPIKeyAdmin(admin.ModelAdmin):
    list_display = ['user', 'name', 'key_prefix', 'is_active', 'is_sandbox', 'created_at', 'last_used_at', 'expires_at']
    list_filter = ['is_active', 'is_sandbox']
    search_fields = ['user__username', 'user__email', 'name', 'key_prefix']
    readonly_fields = ['key_prefix', 'key_hash', 'created_at', 'last_used_at']
    ordering = ['-created_at']


@admin.register(ChangelogEntry)
class ChangelogEntryAdmin(admin.ModelAdmin):
    list_display = ['version', 'release_date', 'change_type', 'title', 'is_breaking']
    list_filter = ['change_type', 'is_breaking', 'version']
    search_fields = ['title', 'description', 'endpoint']
    ordering = ['-release_date']
