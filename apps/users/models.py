from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _
from django.utils import timezone


class SubscriptionTier(models.TextChoices):
    FREE = 'FREE', _('Free')
    PRO = 'PRO', _('Pro')
    PREMIUM = 'PREMIUM', _('Premium')


class UserProfile(models.Model):
    """
    Extended user profile with subscription and usage information.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile'
    )
    phone_number = models.CharField(
        _('Phone Number'),
        max_length=20,
        blank=True
    )
    company = models.CharField(
        _('Company'),
        max_length=255,
        blank=True
    )
    email_verified = models.BooleanField(
        _('Email Verified'),
        default=False
    )
    email_verification_token = models.CharField(
        max_length=100,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('User Profile')
        verbose_name_plural = _('User Profiles')
    
    def __str__(self):
        return f"{self.user.username}'s profile"
    
    @property
    def subscription_tier(self):
        """Get the user's current active subscription tier."""
        active_sub = self.user.subscriptions.filter(
            is_active=True,
            end_date__gte=timezone.now()
        ).first()
        return active_sub.tier if active_sub else SubscriptionTier.FREE
    
    @property
    def is_premium(self):
        return self.subscription_tier == SubscriptionTier.PREMIUM
    
    @property
    def is_pro(self):
        return self.subscription_tier == SubscriptionTier.PRO


class Subscription(models.Model):
    """
    User subscription model for managing SaaS tiers.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='subscriptions'
    )
    tier = models.CharField(
        _('Subscription Tier'),
        max_length=20,
        choices=SubscriptionTier.choices,
        default=SubscriptionTier.FREE
    )
    stripe_subscription_id = models.CharField(
        _('Stripe Subscription ID'),
        max_length=255,
        blank=True,
        unique=True,
        null=True
    )
    stripe_customer_id = models.CharField(
        _('Stripe Customer ID'),
        max_length=255,
        blank=True
    )
    is_active = models.BooleanField(
        _('Is Active'),
        default=True
    )
    start_date = models.DateTimeField(
        _('Start Date'),
        default=timezone.now
    )
    end_date = models.DateTimeField(
        _('End Date'),
        null=True,
        blank=True
    )
    auto_renew = models.BooleanField(
        _('Auto Renew'),
        default=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('Subscription')
        verbose_name_plural = _('Subscriptions')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['stripe_subscription_id']),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.get_tier_display()}"
    
    @property
    def is_expired(self):
        if not self.end_date:
            return False
        return timezone.now() > self.end_date
    
    def cancel(self):
        """Cancel the subscription."""
        self.is_active = False
        self.auto_renew = False
        self.save()


class APIUsage(models.Model):
    """
    Track API usage for rate limiting and analytics.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='api_usage',
        null=True,
        blank=True
    )
    endpoint = models.CharField(
        _('Endpoint'),
        max_length=255
    )
    method = models.CharField(
        _('HTTP Method'),
        max_length=10
    )
    timestamp = models.DateTimeField(
        _('Timestamp'),
        auto_now_add=True,
        db_index=True
    )
    response_status = models.IntegerField(
        _('Response Status'),
        null=True
    )
    ip_address = models.GenericIPAddressField(
        _('IP Address'),
        null=True,
        blank=True
    )
    
    class Meta:
        verbose_name = _('API Usage')
        verbose_name_plural = _('API Usage')
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['endpoint', 'timestamp']),
        ]
    
    def __str__(self):
        user_display = self.user.username if self.user else 'Anonymous'
        return f"{user_display} - {self.method} {self.endpoint}"
