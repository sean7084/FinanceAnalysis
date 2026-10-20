from channels.generic.websocket import AsyncJsonWebsocketConsumer

ALERTS_GROUP = 'alerts'


class AlertConsumer(AsyncJsonWebsocketConsumer):
    """
    Streams alert notifications to every connected client.
    """

    async def connect(self):
        await self.channel_layer.group_add(ALERTS_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(ALERTS_GROUP, self.channel_name)

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
