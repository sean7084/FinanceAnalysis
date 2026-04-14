from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def _get_user_by_id(user_id):
    User = get_user_model()
    try:
        return User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return None


class AlertConsumer(AsyncJsonWebsocketConsumer):
    """
    Streams alert notifications to an authenticated user.
    """

    async def connect(self):
        user = self.scope.get('user')

        if not user or user.is_anonymous:
            try:
                raw_qs = self.scope.get('query_string', b'').decode('utf-8')
                token = parse_qs(raw_qs).get('token', [''])[0]
                if token:
                    payload = AccessToken(token)
                    user = await _get_user_by_id(payload.get('user_id'))
            except Exception:
                user = None

        if not user or user.is_anonymous:
            await self.close()
            return

        self.group_name = f'alerts_user_{user.id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def alert_message(self, event):
        await self.send_json(
            {
                'type': 'alert',
                'event_id': event.get('event_id'),
                'asset_symbol': event.get('asset_symbol'),
                'alert_name': event.get('alert_name'),
                'message': event.get('message'),
                'created_at': event.get('created_at'),
            }
        )
