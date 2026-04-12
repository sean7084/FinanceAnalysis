from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from rest_framework.test import APIClient
from rest_framework import status

from .models import UserProfile, Subscription, APIUsage, SubscriptionTier


def make_user(username='testuser', email='test@example.com', password='TestPass123!'):
    """Helper to create a user with a verified profile."""
    user = User.objects.create_user(username=username, email=email, password=password)
    user.profile.email_verified = True
    user.profile.save()
    return user


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class UserRegistrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/v1/users/register/'
        self.valid_data = {
            'username': 'newuser',
            'email': 'newuser@example.com',
            'password': 'Secure@Pass99',
            'password_confirm': 'Secure@Pass99',
            'first_name': 'New',
            'last_name': 'User',
        }

    def test_registration_creates_user_and_profile(self):
        response = self.client.post(self.url, self.valid_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(username='newuser').exists())
        user = User.objects.get(username='newuser')
        self.assertTrue(hasattr(user, 'profile'))
        # Token should be set for email verification
        self.assertNotEqual(user.profile.email_verification_token, '')

    def test_registration_password_mismatch_fails(self):
        data = {**self.valid_data, 'password_confirm': 'WrongPass99'}
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_registration_duplicate_email_fails(self):
        make_user(username='existing', email='newuser@example.com')
        response = self.client.post(self.url, self.valid_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_registration_weak_password_fails(self):
        data = {**self.valid_data, 'password': '123', 'password_confirm': '123'}
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class EmailVerificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/v1/users/verify-email/'
        self.user = User.objects.create_user(
            username='verifyme', email='verify@example.com', password='TestPass123!'
        )
        self.user.profile.email_verification_token = 'valid-token-abc123'
        self.user.profile.email_verified = False
        self.user.profile.save()

    def test_valid_token_verifies_email(self):
        response = self.client.post(self.url, {'token': 'valid-token-abc123'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.email_verified)
        self.assertEqual(self.user.profile.email_verification_token, '')

    def test_invalid_token_returns_400(self):
        response = self.client.post(self.url, {'token': 'bad-token'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_already_verified_returns_400(self):
        self.user.profile.email_verified = True
        self.user.profile.save()
        response = self.client.post(self.url, {'token': 'valid-token-abc123'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordResetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user()

    def test_password_reset_request_always_returns_200(self):
        # Non-existent email should still return 200 (no enumeration)
        response = self.client.post(
            '/api/v1/users/password-reset/',
            {'email': 'nobody@example.com'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_password_reset_request_sets_token(self):
        response = self.client.post(
            '/api/v1/users/password-reset/',
            {'email': 'test@example.com'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.profile.refresh_from_db()
        self.assertNotEqual(self.user.profile.email_verification_token, '')

    def test_password_reset_confirm_changes_password(self):
        # Set up a token first
        self.user.profile.email_verification_token = 'reset-token-xyz'
        self.user.profile.save()

        response = self.client.post(
            '/api/v1/users/password-reset-confirm/',
            {
                'token': 'reset-token-xyz',
                'password': 'NewSecure@Pass99',
                'password_confirm': 'NewSecure@Pass99',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewSecure@Pass99'))
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.email_verification_token, '')

    def test_password_reset_confirm_invalid_token_returns_400(self):
        response = self.client.post(
            '/api/v1/users/password-reset-confirm/',
            {
                'token': 'nonexistent-token',
                'password': 'NewSecure@Pass99',
                'password_confirm': 'NewSecure@Pass99',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class UserProfileTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user()
        self.client.force_authenticate(user=self.user)
        self.url = '/api/v1/users/profile/me/'

    def test_get_profile_returns_user_data(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('user', response.data)
        self.assertIn('profile', response.data)
        self.assertEqual(response.data['user']['username'], 'testuser')

    def test_patch_profile_updates_fields(self):
        response = self.client.patch(
            self.url,
            {'phone_number': '+1234567890', 'company': 'Acme Inc'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.phone_number, '+1234567890')
        self.assertEqual(self.user.profile.company, 'Acme Inc')

    def test_profile_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user()
        self.client.force_authenticate(user=self.user)

    def test_current_subscription_returns_free_tier_when_none(self):
        response = self.client.get('/api/v1/users/subscriptions/current/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['tier'], SubscriptionTier.FREE)

    def test_current_subscription_returns_active_pro_subscription(self):
        Subscription.objects.create(
            user=self.user,
            tier=SubscriptionTier.PRO,
            is_active=True,
            end_date=timezone.now() + timedelta(days=30),
        )
        response = self.client.get('/api/v1/users/subscriptions/current/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['tier'], SubscriptionTier.PRO)

    def test_expired_subscription_falls_back_to_free(self):
        Subscription.objects.create(
            user=self.user,
            tier=SubscriptionTier.PREMIUM,
            is_active=True,
            end_date=timezone.now() - timedelta(days=1),  # already expired
        )
        # profile.subscription_tier queries end_date__gte=now, so this returns FREE
        self.assertEqual(self.user.profile.subscription_tier, SubscriptionTier.FREE)

    def test_subscription_list_scoped_to_user(self):
        other = make_user(username='other', email='other@example.com')
        Subscription.objects.create(user=other, tier=SubscriptionTier.PRO, is_active=True)
        response = self.client.get('/api/v1/users/subscriptions/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Our user has no subscriptions; other user's record must not appear
        for sub in response.data['results']:
            self.assertEqual(sub['username'], 'testuser')

    def test_subscription_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/api/v1/users/subscriptions/current/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class APIUsageStatsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user()
        self.client.force_authenticate(user=self.user)

    def _create_usage(self, n, endpoint='/api/v1/markets/'):
        APIUsage.objects.bulk_create([
            APIUsage(
                user=self.user,
                endpoint=endpoint,
                method='GET',
                response_status=200,
                ip_address='127.0.0.1',
            )
            for _ in range(n)
        ])

    def test_stats_returns_daily_and_monthly_counts(self):
        self._create_usage(5)
        response = self.client.get('/api/v1/users/usage/stats/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['daily_usage'], 5)
        self.assertGreaterEqual(response.data['monthly_usage'], 5)

    def test_stats_shows_free_tier_limit(self):
        response = self.client.get('/api/v1/users/usage/stats/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['subscription_tier'], SubscriptionTier.FREE)
        self.assertEqual(response.data['daily_limit'], 100)

    def test_stats_scoped_to_current_user(self):
        other = make_user(username='other2', email='other2@example.com')
        APIUsage.objects.create(
            user=other, endpoint='/api/v1/markets/', method='GET',
            response_status=200, ip_address='127.0.0.1',
        )
        self._create_usage(3)
        response = self.client.get('/api/v1/users/usage/stats/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Our usage count should be exactly 3, not 4
        self.assertEqual(response.data['daily_usage'], 3)

    def test_stats_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/api/v1/users/usage/stats/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SubscriptionTierPropertyTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_is_pro_true_with_active_pro_subscription(self):
        Subscription.objects.create(
            user=self.user,
            tier=SubscriptionTier.PRO,
            is_active=True,
            end_date=timezone.now() + timedelta(days=30),
        )
        self.assertTrue(self.user.profile.is_pro)
        self.assertFalse(self.user.profile.is_premium)

    def test_is_premium_true_with_active_premium_subscription(self):
        Subscription.objects.create(
            user=self.user,
            tier=SubscriptionTier.PREMIUM,
            is_active=True,
            end_date=timezone.now() + timedelta(days=30),
        )
        self.assertTrue(self.user.profile.is_premium)
        self.assertFalse(self.user.profile.is_pro)

    def test_no_subscription_defaults_to_free(self):
        self.assertFalse(self.user.profile.is_pro)
        self.assertFalse(self.user.profile.is_premium)
        self.assertEqual(self.user.profile.subscription_tier, SubscriptionTier.FREE)
