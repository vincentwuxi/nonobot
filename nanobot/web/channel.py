"""Web channel — delivers agent responses to browser WebSocket clients."""

from __future__ import annotations

import asyncio
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

    Final responses are streamed token-by-token for a typewriter effect.
    """

    name = "web"
    display_name = "Web"

    # Streaming config
    STREAM_CHUNK_SIZE = 6   # characters per chunk
    STREAM_DELAY = 0.02     # seconds between chunks

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
        """Send a message to the appropriate WebSocket client.

        Final messages are streamed chunk-by-chunk for typewriter effect.
        Progress/tool_hint messages are sent immediately.
        """
        from nanobot.web.shared import manager

        is_progress = msg.metadata.get("_progress", False)
        is_tool_hint = msg.metadata.get("_tool_hint", False)
        content = msg.content or ""

        # Determine target
        conn_id = msg.chat_id
        target_active = conn_id in manager.active

        async def _send(data: dict) -> None:
            if target_active:
                await manager.send_json(conn_id, data)
            else:
                await manager.broadcast(data)

        # Progress / tool_hint — send immediately (no streaming)
        if is_progress:
            await _send({
                "type": "tool_hint" if is_tool_hint else "progress",
                "content": content,
                "channel": msg.channel,
                "chat_id": msg.chat_id,
            })
            return

        # Final message — stream it chunk by chunk
        if content and len(content) > 20:
            # Send stream_start
            await _send({
                "type": "stream_start",
                "channel": msg.channel,
                "chat_id": msg.chat_id,
            })

            # Stream chunks
            pos = 0
            while pos < len(content):
                chunk = content[pos:pos + self.STREAM_CHUNK_SIZE]
                await _send({
                    "type": "stream",
                    "content": chunk,
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                })
                pos += self.STREAM_CHUNK_SIZE
                await asyncio.sleep(self.STREAM_DELAY)

            # Send stream_end with full content for final render
            await _send({
                "type": "stream_end",
                "content": content,
                "channel": msg.channel,
                "chat_id": msg.chat_id,
            })
        else:
            # Short messages — send directly
            await _send({
                "type": "message",
                "content": content,
                "channel": msg.channel,
                "chat_id": msg.chat_id,
            })

    def is_allowed(self, sender_id: str) -> bool:
        """Web channel allows all connections."""
        return True

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return {"enabled": True, "allowFrom": ["*"]}

