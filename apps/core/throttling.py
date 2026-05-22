from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from apps.users.models import SubscriptionTier


class AuthEndpointRateThrottle(AnonRateThrottle):
    """
    Dedicated rate throttle for anonymous JWT auth endpoints.

    This keeps login/refresh traffic independent from the global anonymous API
    rate so local development can relax auth limits without weakening the rest
    of the API surface.
    """

    scope = 'auth'


class TierBasedRateThrottle(UserRateThrottle):
    """
    Base class for tier-based rate throttling.
    """
    
    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            # Use user ID + tier for cache key
            tier = request.user.profile.subscription_tier
            ident = f"{request.user.pk}:{tier}"
        else:
            # Anonymous users
            ident = self.get_ident(request)
        
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }


class FreeUserRateThrottle(TierBasedRateThrottle):
    """
    Free tier users: 100 requests per day.
    """
    scope = 'free'
    
    def allow_request(self, request, view):
        if request.user and request.user.is_authenticated:
            tier = request.user.profile.subscription_tier
            if tier != SubscriptionTier.FREE:
                # Not a free user, skip this throttle
                return True
        return super().allow_request(request, view)


class ProUserRateThrottle(TierBasedRateThrottle):
    """
    Pro tier users: 1000 requests per day.
    """
    scope = 'pro'
    
    def allow_request(self, request, view):
        if request.user and request.user.is_authenticated:
            tier = request.user.profile.subscription_tier
            if tier != SubscriptionTier.PRO:
                # Not a pro user, skip this throttle
                return True
        return super().allow_request(request, view)


class PremiumUserRateThrottle(TierBasedRateThrottle):
    """
    Premium tier users: 10000 requests per day.
    """
    scope = 'premium'
    
    def allow_request(self, request, view):
        if request.user and request.user.is_authenticated:
            tier = request.user.profile.subscription_tier
            if tier != SubscriptionTier.PREMIUM:
                # Not a premium user, skip this throttle
                return True
        return super().allow_request(request, view)


class BasicUserRateThrottle(UserRateThrottle):
    """
    Legacy throttle for backward compatibility.
    Applies to all authenticated users who aren't in a specific tier.
    """
    scope = 'user'
