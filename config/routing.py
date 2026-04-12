from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

from apps.analytics.routing import websocket_urlpatterns


application = ProtocolTypeRouter(
    {
        'websocket': AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        ),
    }
)
