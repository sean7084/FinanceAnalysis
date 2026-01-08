from rest_framework.throttling import UserRateThrottle


class PremiumUserRateThrottle(UserRateThrottle):
    """
    Premium tier users get higher rate limits.
    To use: Add 'is_premium' attribute to User model or use groups.
    """
    scope = 'premium'
    
    def allow_request(self, request, view):
        # Check if user is premium (you can implement this based on your user model)
        if request.user and request.user.is_authenticated:
            # Example: Check if user has premium group or subscription
            # if hasattr(request.user, 'is_premium') and request.user.is_premium:
            #     return super().allow_request(request, view)
            # For now, use the default 'user' throttle for all authenticated users
            pass
        return super().allow_request(request, view)


class BasicUserRateThrottle(UserRateThrottle):
    """
    Basic authenticated users (non-premium).
    """
    scope = 'user'
