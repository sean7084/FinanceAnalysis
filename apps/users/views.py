from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from datetime import timedelta
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
