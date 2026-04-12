from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import UserProfile, Subscription, APIUsage


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile'
    fields = ['phone_number', 'company', 'email_verified', 'created_at', 'updated_at']
    readonly_fields = ['created_at', 'updated_at']


class SubscriptionInline(admin.TabularInline):
    model = Subscription
    extra = 0
    fields = ['tier', 'is_active', 'start_date', 'end_date', 'auto_renew']
    readonly_fields = ['start_date', 'created_at']


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline, SubscriptionInline)
    list_display = ('username', 'email', 'first_name', 'last_name', 'get_subscription_tier', 'is_staff')
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'date_joined')
    
    def get_subscription_tier(self, obj):
        return obj.profile.subscription_tier if hasattr(obj, 'profile') else 'N/A'
    get_subscription_tier.short_description = 'Subscription Tier'


# Re-register UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'tier', 'is_active', 'start_date', 'end_date', 'auto_renew']
    list_filter = ['tier', 'is_active', 'auto_renew', 'created_at']
    search_fields = ['user__username', 'user__email', 'stripe_subscription_id']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Subscription Details', {
            'fields': ('tier', 'is_active', 'start_date', 'end_date', 'auto_renew')
        }),
        ('Stripe Information', {
            'fields': ('stripe_subscription_id', 'stripe_customer_id'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['activate_subscription', 'deactivate_subscription']
    
    def activate_subscription(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, f"{queryset.count()} subscriptions activated.")
    activate_subscription.short_description = "Activate selected subscriptions"
    
    def deactivate_subscription(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f"{queryset.count()} subscriptions deactivated.")
    deactivate_subscription.short_description = "Deactivate selected subscriptions"


@admin.register(APIUsage)
class APIUsageAdmin(admin.ModelAdmin):
    list_display = ['user', 'endpoint', 'method', 'response_status', 'timestamp', 'ip_address']
    list_filter = ['method', 'response_status', 'timestamp']
    search_fields = ['user__username', 'endpoint', 'ip_address']
    readonly_fields = ['timestamp']
    date_hierarchy = 'timestamp'
    
    fieldsets = (
        ('Request Information', {
            'fields': ('user', 'endpoint', 'method', 'ip_address')
        }),
        ('Response Information', {
            'fields': ('response_status', 'timestamp')
        }),
    )
