from django.utils.deprecation import MiddlewareMixin
from django.utils import timezone
from apps.users.models import APIUsage


class APIUsageMiddleware(MiddlewareMixin):
    """
    Middleware to track API usage for authenticated and anonymous users.
    """
    
    def process_response(self, request, response):
        # Only track API requests
        if request.path.startswith('/api/v1/'):
            # Get user (None for anonymous)
            user = request.user if request.user.is_authenticated else None
            
            # Get IP address
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip_address = x_forwarded_for.split(',')[0]
            else:
                ip_address = request.META.get('REMOTE_ADDR')
            
            # Create usage record
            APIUsage.objects.create(
                user=user,
                endpoint=request.path,
                method=request.method,
                response_status=response.status_code,
                ip_address=ip_address
            )
        
        return response
