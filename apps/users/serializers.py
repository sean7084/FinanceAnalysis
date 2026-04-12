from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from .models import UserProfile, Subscription, APIUsage, SubscriptionTier
import secrets


class UserProfileSerializer(serializers.ModelSerializer):
    subscription_tier = serializers.CharField(read_only=True)
    is_premium = serializers.BooleanField(read_only=True)
    is_pro = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = UserProfile
        fields = [
            'phone_number',
            'company',
            'email_verified',
            'subscription_tier',
            'is_premium',
            'is_pro',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['email_verified', 'created_at', 'updated_at']


class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)
    
    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'profile',
            'date_joined',
        ]
        read_only_fields = ['id', 'date_joined']


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={'input_type': 'password'}
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'}
    )
    phone_number = serializers.CharField(required=False, allow_blank=True)
    company = serializers.CharField(required=False, allow_blank=True)
    
    class Meta:
        model = User
        fields = [
            'username',
            'email',
            'password',
            'password_confirm',
            'first_name',
            'last_name',
            'phone_number',
            'company',
        ]
    
    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({
                "password": "Password fields didn't match."
            })
        
        if User.objects.filter(email=attrs['email']).exists():
            raise serializers.ValidationError({
                "email": "A user with this email already exists."
            })
        
        return attrs
    
    def create(self, validated_data):
        # Remove password_confirm from validated_data
        validated_data.pop('password_confirm')
        
        # Extract profile fields
        phone_number = validated_data.pop('phone_number', '')
        company = validated_data.pop('company', '')
        
        # Create user
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
        )
        
        # Update profile (automatically created by signal)
        profile = user.profile
        profile.phone_number = phone_number
        profile.company = company
        profile.email_verification_token = secrets.token_urlsafe(32)
        profile.save()
        
        return user


class SubscriptionSerializer(serializers.ModelSerializer):
    tier_display = serializers.CharField(source='get_tier_display', read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    
    class Meta:
        model = Subscription
        fields = [
            'id',
            'username',
            'tier',
            'tier_display',
            'is_active',
            'is_expired',
            'start_date',
            'end_date',
            'auto_renew',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'stripe_subscription_id',
            'stripe_customer_id',
            'created_at',
            'updated_at',
        ]


class APIUsageSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    
    class Meta:
        model = APIUsage
        fields = [
            'id',
            'username',
            'endpoint',
            'method',
            'timestamp',
            'response_status',
            'ip_address',
        ]
        read_only_fields = ['id', 'timestamp']


class EmailVerificationSerializer(serializers.Serializer):
    token = serializers.CharField(required=True)
    
    def validate_token(self, value):
        try:
            profile = UserProfile.objects.get(email_verification_token=value)
            if profile.email_verified:
                raise serializers.ValidationError("Email already verified.")
        except UserProfile.DoesNotExist:
            raise serializers.ValidationError("Invalid verification token.")
        
        self.context['profile'] = profile
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    
    def validate_email(self, value):
        try:
            user = User.objects.get(email=value)
            self.context['user'] = user
        except User.DoesNotExist:
            # Don't reveal if email exists or not for security
            pass
        
        return value


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(required=True)
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={'input_type': 'password'}
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'}
    )
    
    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({
                "password": "Password fields didn't match."
            })
        
        try:
            profile = UserProfile.objects.get(email_verification_token=attrs['token'])
            self.context['user'] = profile.user
        except UserProfile.DoesNotExist:
            raise serializers.ValidationError({
                "token": "Invalid reset token."
            })
        
        return attrs
