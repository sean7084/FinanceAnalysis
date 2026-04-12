import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class DeveloperAPIKey(models.Model):
    """
    Secure API key for developer portal access.

    The raw key is returned only once at creation. Only the SHA-256 hash
    is stored in the database. The ``key_prefix`` (first 12 characters)
    is stored in plain text so users can identify their keys.

    Key format: ``fa-<40 hex chars>``  (total 43 characters)
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='api_keys',
        verbose_name=_('User'),
    )
    name = models.CharField(
        _('Key Name'),
        max_length=100,
        help_text=_('A friendly label for this key, e.g. "Production" or "CI pipeline".'),
    )
    key_prefix = models.CharField(
        _('Key Prefix'),
        max_length=12,
        editable=False,
        help_text=_('First 12 characters of the key (safe to display).'),
    )
    key_hash = models.CharField(
        _('Key Hash'),
        max_length=64,
        unique=True,
        editable=False,
        help_text=_('SHA-256 hash of the full key. Never stored in plain text.'),
    )
    is_active = models.BooleanField(_('Active'), default=True)
    is_sandbox = models.BooleanField(
        _('Sandbox Mode'),
        default=False,
        help_text=_('Sandbox keys are limited to read-only operations and synthetic data.'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(_('Last Used At'), null=True, blank=True)
    expires_at = models.DateTimeField(
        _('Expires At'),
        null=True,
        blank=True,
        help_text=_('Leave blank for non-expiring keys.'),
    )

    class Meta:
        verbose_name = _('Developer API Key')
        verbose_name_plural = _('Developer API Keys')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['key_hash']),
            models.Index(fields=['user', 'is_active']),
        ]

    def __str__(self):
        status = 'active' if self.is_active else 'revoked'
        return f'{self.user.username} / {self.name} [{self.key_prefix}…] ({status})'

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at < timezone.now()

    # ------------------------------------------------------------------
    # Class-level factory — call this to mint a new key
    # ------------------------------------------------------------------

    @classmethod
    def generate(cls, user, name, is_sandbox=False, expires_at=None):
        """
        Create a new API key.  Returns ``(instance, raw_key)``.

        ``raw_key`` is the only time the full key string is available;
        it is NOT stored in the database.
        """
        raw_key = 'fa-' + secrets.token_hex(20)   # "fa-" + 40 hex = 43 chars
        key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
        key_prefix = raw_key[:12]
        instance = cls.objects.create(
            user=user,
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            is_sandbox=is_sandbox,
            expires_at=expires_at,
        )
        return instance, raw_key


class ChangelogEntry(models.Model):
    """
    API version changelog.  Records breaking changes, new features, fixes, etc.
    Returned by the public ``/api/v1/developer/changelog/`` endpoint.
    """

    class ChangeType(models.TextChoices):
        ADDED = 'ADDED', _('Added')
        CHANGED = 'CHANGED', _('Changed')
        DEPRECATED = 'DEPRECATED', _('Deprecated')
        REMOVED = 'REMOVED', _('Removed')
        FIXED = 'FIXED', _('Fixed')
        SECURITY = 'SECURITY', _('Security')

    version = models.CharField(
        _('API Version'),
        max_length=20,
        help_text=_('Semantic version string, e.g. "1.1.0".'),
    )
    release_date = models.DateField(_('Release Date'))
    change_type = models.CharField(
        _('Change Type'),
        max_length=20,
        choices=ChangeType.choices,
        default=ChangeType.ADDED,
    )
    title = models.CharField(_('Title'), max_length=200)
    description = models.TextField(_('Description'), blank=True)
    is_breaking = models.BooleanField(
        _('Breaking Change'),
        default=False,
        help_text=_('Flag if this change is not backward-compatible.'),
    )
    endpoint = models.CharField(
        _('Affected Endpoint'),
        max_length=200,
        blank=True,
        help_text=_('e.g. "POST /api/v1/backtest/"'),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Changelog Entry')
        verbose_name_plural = _('Changelog Entries')
        ordering = ['-release_date', '-created_at']
        indexes = [
            models.Index(fields=['version']),
            models.Index(fields=['release_date']),
        ]

    def __str__(self):
        breaking = ' [BREAKING]' if self.is_breaking else ''
        return f'[{self.version}] {self.change_type} — {self.title}{breaking}'
