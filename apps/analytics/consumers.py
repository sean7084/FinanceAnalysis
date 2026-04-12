from channels.generic.websocket import AsyncJsonWebsocketConsumer


class AlertConsumer(AsyncJsonWebsocketConsumer):
    """
    Streams alert notifications to an authenticated user.
    """

    async def connect(self):
        user = self.scope.get('user')
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
