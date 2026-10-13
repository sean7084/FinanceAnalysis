from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
import secrets

from .models import UserProfile, Subscription, APIUsage, SubscriptionTier
from .serializers import (
    UserSerializer,
    UserRegistrationSerializer,
    UserProfileSerializer,
    SubscriptionSerializer,
    APIUsageSerializer,
    EmailVerificationSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
)
from apps.core.throttling import AuthEndpointRateThrottle


class ScopedTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [AuthEndpointRateThrottle]


class ScopedTokenRefreshView(TokenRefreshView):
    throttle_classes = [AuthEndpointRateThrottle]


class ScopedTokenVerifyView(TokenVerifyView):
    throttle_classes = [AuthEndpointRateThrottle]


# Cookie-based auth views
# ------------------------------------------------------------------------------
# The SPA no longer stores the refresh token in JS-readable storage: the login
# and refresh endpoints below move it into an HttpOnly cookie (see the
# AUTH_COOKIE_* block in config/settings/base.py). Access tokens stay in JS
# memory on the client, so we still return them in the response body.
#
# The legacy /api/v1/auth/token/ and /api/v1/auth/token/refresh/ endpoints
# above are kept intact so Swagger, curl scripts, and non-browser clients keep
# working against the same JWT store.


def _cookie_samesite() -> str:
    """Normalise AUTH_COOKIE_SAMESITE to Django's expected title-case values."""
    value = str(settings.AUTH_COOKIE_SAMESITE or '').strip().capitalize()
    return value if value in ('Lax', 'Strict', 'None') else 'Lax'


def _set_refresh_cookie(response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.AUTH_COOKIE_MAX_AGE,
        path=settings.AUTH_COOKIE_PATH,
        domain=settings.AUTH_COOKIE_DOMAIN or None,
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=settings.AUTH_COOKIE_HTTP_ONLY,
        samesite=_cookie_samesite(),
    )


def _delete_refresh_cookie(response) -> None:
    response.delete_cookie(
        key=settings.AUTH_COOKIE_NAME,
        path=settings.AUTH_COOKIE_PATH,
        domain=settings.AUTH_COOKIE_DOMAIN or None,
        samesite=_cookie_samesite(),
    )


class CookieTokenObtainPairView(TokenObtainPairView):
    """
    POST /api/v1/auth/login/

    Same credentials flow as TokenObtainPairView, but the returned refresh
    token is stripped from the response body and set as an HttpOnly cookie.
    The access token stays in the JSON body so the SPA can cache it in memory.
    """

    throttle_classes = [AuthEndpointRateThrottle]

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK and isinstance(response.data, dict):
            refresh = response.data.pop('refresh', None)
            if refresh:
                _set_refresh_cookie(response, refresh)
        return response


class CookieTokenRefreshView(APIView):
    """
    POST /api/v1/auth/refresh/

    Reads the refresh token from the HttpOnly cookie set by /auth/login/ or a
    previous /auth/refresh/ call. Rotates the cookie (SimpleJWT rotation is on,
    blacklist-after-rotation is on) and returns only the new access token in
    the response body. Falls back to a `{"refresh": ...}` body for non-browser
    clients; the browser SPA never sends a body.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [AuthEndpointRateThrottle]

    def _extract_refresh(self, request) -> str:
        cookie_value = request.COOKIES.get(settings.AUTH_COOKIE_NAME, '') or ''
        if cookie_value:
            return cookie_value.strip()
        body = request.data if isinstance(request.data, dict) else {}
        body_value = body.get('refresh') if isinstance(body, dict) else None
        if isinstance(body_value, str) and body_value.strip():
            return body_value.strip()
        return ''

    def post(self, request, *args, **kwargs):
        incoming = self._extract_refresh(request)
        if not incoming:
            return Response(
                {'detail': 'no_refresh_token', 'code': 'not_authenticated'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = TokenRefreshSerializer(data={'refresh': incoming})
        try:
            serializer.is_valid(raise_exception=True)
        except (TokenError, DRFValidationError):
            response = Response(
                {'detail': 'invalid_refresh_token', 'code': 'refresh_revoked'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
            _delete_refresh_cookie(response)
            return response

        data = dict(serializer.validated_data)
        rotated = data.pop('refresh', None)
        response = Response(data, status=status.HTTP_200_OK)
        if rotated and rotated != incoming:
            _set_refresh_cookie(response, rotated)
        return response


class CookieTokenLogoutView(APIView):
    """
    POST /api/v1/auth/logout/

    Blacklists the current refresh token (best-effort -- already-blacklisted or
    malformed tokens are silently ignored) and clears the cookie. Always
    returns 204 so the SPA can rely on it during cleanup.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [AuthEndpointRateThrottle]

    def post(self, request, *args, **kwargs):
        raw = request.COOKIES.get(settings.AUTH_COOKIE_NAME, '') or ''
        if raw:
            try:
                RefreshToken(raw).blacklist()
            except TokenError:
                pass
        response = Response(status=status.HTTP_204_NO_CONTENT)
        _delete_refresh_cookie(response)
        return response


class SessionStateView(APIView):
    """
    GET /api/v1/auth/session/

    Cheap bootstrap probe used by the SPA on load. Reports whether a valid
    refresh cookie is present so the client can decide to call /auth/refresh/
    immediately. Never returns the refresh token itself.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request, *args, **kwargs):
        raw = request.COOKIES.get(settings.AUTH_COOKIE_NAME, '') or ''
        if not raw:
            return Response({'authenticated': False})

        try:
            refresh = RefreshToken(raw)
        except TokenError:
            return Response({'authenticated': False})

        user_id_claim = settings.SIMPLE_JWT.get('USER_ID_CLAIM', 'user_id')
        user_id = refresh.payload.get(user_id_claim)
        User = get_user_model()
        try:
            user = User.objects.get(id=user_id, is_active=True)
        except (User.DoesNotExist, TypeError, ValueError):
            return Response({'authenticated': False})

        exp = refresh.payload.get('exp')
        refresh_expires_at = (
            datetime.fromtimestamp(int(exp), tz=dt_timezone.utc).isoformat()
            if exp is not None
            else None
        )

        return Response({
            'authenticated': True,
            'user_id': user.id,
            'username': user.username,
            'refresh_expires_at': refresh_expires_at,
        })


class UserRegistrationView(APIView):
    """
    User registration endpoint.
    POST /api/v1/users/register/
    """
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            
            # Send verification email
            verification_url = f"{settings.FRONTEND_URL}/verify-email/{user.profile.email_verification_token}"
            send_mail(
                'Verify your email - FinanceAnalysis',
                f'Please click the link to verify your email: {verification_url}',
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            
            return Response({
                'message': 'Registration successful. Please check your email to verify your account.',
                'user': UserSerializer(user).data
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class EmailVerificationView(APIView):
    """
    Email verification endpoint.
    POST /api/v1/users/verify-email/
    Body: {"token": "..."}
    """
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        serializer = EmailVerificationSerializer(data=request.data)
        if serializer.is_valid():
            profile = serializer.context['profile']
            profile.email_verified = True
            profile.email_verification_token = ''
            profile.save()
            
            return Response({
                'message': 'Email verified successfully.'
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetRequestView(APIView):
    """
    Request password reset.
    POST /api/v1/users/password-reset/
    Body: {"email": "user@example.com"}
    """
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.context.get('user')
            
            if user:
                # Generate reset token
                profile = user.profile
                profile.email_verification_token = secrets.token_urlsafe(32)
                profile.save()
                
                # Send reset email
                reset_url = f"{settings.FRONTEND_URL}/reset-password/{profile.email_verification_token}"
                send_mail(
                    'Password Reset - FinanceAnalysis',
                    f'Please click the link to reset your password: {reset_url}',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
            
            # Always return success to prevent email enumeration
            return Response({
                'message': 'If the email exists, a password reset link has been sent.'
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetConfirmView(APIView):
    """
    Confirm password reset.
    POST /api/v1/users/password-reset-confirm/
    Body: {"token": "...", "password": "...", "password_confirm": "..."}
    """
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.context['user']
            user.set_password(serializer.validated_data['password'])
            user.save()
            
            # Clear the token
            user.profile.email_verification_token = ''
            user.profile.save()
            
            return Response({
                'message': 'Password reset successfully.'
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserProfileViewSet(viewsets.ModelViewSet):
    """
    User profile management.
    GET /api/v1/users/profile/me/ - Get current user's profile
    PUT /api/v1/users/profile/me/ - Update current user's profile
    """
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return UserProfile.objects.filter(user=self.request.user)
    
    @action(detail=False, methods=['get', 'put', 'patch'])
    def me(self, request):
        """Get or update current user's profile."""
        profile = request.user.profile
        
        if request.method == 'GET':
            serializer = UserProfileSerializer(profile)
            user_data = UserSerializer(request.user).data
            return Response({
                'user': user_data,
                'profile': serializer.data
            })
        
        elif request.method in ['PUT', 'PATCH']:
            partial = request.method == 'PATCH'
            serializer = UserProfileSerializer(profile, data=request.data, partial=partial)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SubscriptionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    User subscriptions (read-only, managed via Stripe).
    GET /api/v1/users/subscriptions/ - List user's subscriptions
    GET /api/v1/users/subscriptions/current/ - Get current active subscription
    """
    serializer_class = SubscriptionSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)
    
    @action(detail=False, methods=['get'])
    def current(self, request):
        """Get current active subscription."""
        active_sub = request.user.subscriptions.filter(
            is_active=True,
            end_date__gte=timezone.now()
        ).first()
        
        if active_sub:
            serializer = SubscriptionSerializer(active_sub)
            return Response(serializer.data)
        
        return Response({
            'tier': SubscriptionTier.FREE,
            'tier_display': 'Free',
            'is_active': True,
            'message': 'User is on free tier'
        })


class APIUsageViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API usage analytics for current user.
    GET /api/v1/users/usage/ - List all API calls
    GET /api/v1/users/usage/stats/ - Get usage statistics
    """
    serializer_class = APIUsageSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return APIUsage.objects.filter(user=self.request.user)
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get usage statistics for the current user."""
        now = timezone.now()
        today = now.date()
        
        # Daily usage
        daily_usage = APIUsage.objects.filter(
            user=request.user,
            timestamp__date=today
        ).count()
        
        # Monthly usage
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly_usage = APIUsage.objects.filter(
            user=request.user,
            timestamp__gte=month_start
        ).count()
        
        # Usage by endpoint (last 7 days)
        week_ago = now - timedelta(days=7)
        endpoint_stats = APIUsage.objects.filter(
            user=request.user,
            timestamp__gte=week_ago
        ).values('endpoint').annotate(
            count=Count('id')
        ).order_by('-count')[:10]
        
        # Get user's tier and limits
        tier = request.user.profile.subscription_tier
        limits = {
            SubscriptionTier.FREE: 100,
            SubscriptionTier.PRO: 1000,
            SubscriptionTier.PREMIUM: 10000,
        }
        daily_limit = limits.get(tier, 100)
        
        return Response({
            'subscription_tier': tier,
            'daily_usage': daily_usage,
            'daily_limit': daily_limit,
            'daily_remaining': max(0, daily_limit - daily_usage),
            'monthly_usage': monthly_usage,
            'top_endpoints': list(endpoint_stats),
            'usage_percentage': round((daily_usage / daily_limit) * 100, 2) if daily_limit > 0 else 0
        })
