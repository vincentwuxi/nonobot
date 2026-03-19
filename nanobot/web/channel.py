"""Web channel — delivers agent responses to browser WebSocket clients."""

from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel


class WebChannel(BaseChannel):
    """Channel that bridges agent responses to WebSocket browser clients.

    Messages are routed here by ChannelManager.send() — we do NOT consume
    from the outbound queue ourselves, so we coexist peacefully with other
    channels.
    """

    name = "web"
    display_name = "Web"

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)

    async def start(self) -> None:
        """Mark channel as running (actual web server is started separately)."""
        self._running = True
        logger.info("WebChannel started")

    async def stop(self) -> None:
        """Stop the web channel."""
        self._running = False
        logger.info("WebChannel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message to the appropriate WebSocket client."""
        from nanobot.web.server import manager

        is_progress = msg.metadata.get("_progress", False)
        is_tool_hint = msg.metadata.get("_tool_hint", False)

        data = {
            "type": "progress" if is_progress else "message",
            "content": msg.content or "",
            "channel": msg.channel,
            "chat_id": msg.chat_id,
        }

        if is_progress:
            data["type"] = "tool_hint" if is_tool_hint else "progress"

        # Route to the specific client
        conn_id = msg.chat_id
        if conn_id in manager.active:
            await manager.send_json(conn_id, data)
        else:
            # Broadcast to all web clients if specific target not found
            await manager.broadcast(data)

    def is_allowed(self, sender_id: str) -> bool:
        """Web channel allows all connections."""
        return True

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return {"enabled": True, "allowFrom": ["*"]}

