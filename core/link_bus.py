"""
Virtual link bus for node-to-node named connections in Pipeline Creator.

Allows a LinkOut to publish data under a named channel, and any number of
LinkIn nodes registered on the same name to receive it without a physical
wire in the node editor.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from loguru import logger


class LinkBus:
    """
    Singleton message bus for virtual named links between modules.

    A LinkOut pushes data to a channel by name.
    Any LinkIn registered on the same name receives that data.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[..., Any]]] = {}

    def subscribe(self, channel: str, callback: Callable[..., Any]) -> None:
        """Register a LinkIn callback on a named channel."""
        channel_clean = channel.strip()
        if not channel_clean:
            logger.warning("LinkBus.subscribe: empty channel name ignored")
            return
        if channel_clean not in self._subscribers:
            self._subscribers[channel_clean] = []
        if callback not in self._subscribers[channel_clean]:
            self._subscribers[channel_clean].append(callback)
            logger.debug(f"LinkBus: subscribed to channel '{channel_clean}'")

    def unsubscribe(self, channel: str, callback: Callable[..., Any]) -> None:
        """Unregister a LinkIn callback from a named channel."""
        channel_clean = channel.strip()
        if channel_clean in self._subscribers:
            try:
                self._subscribers[channel_clean].remove(callback)
                logger.debug(f"LinkBus: unsubscribed from channel '{channel_clean}'")
            except ValueError:
                pass
            if not self._subscribers[channel_clean]:
                del self._subscribers[channel_clean]

    def publish(self, channel: str, *args: Any, **kwargs: Any) -> None:
        """
        Push data to all LinkIn nodes registered on the given channel.
        Silently does nothing if no subscribers exist.
        """
        channel_clean = channel.strip()
        subscribers = self._subscribers.get(channel_clean, [])
        if not subscribers:
            logger.trace(f"LinkBus: no subscribers on channel '{channel_clean}'")
            return
        for cb in list(subscribers):
            try:
                cb(*args, **kwargs)
            except Exception as e:
                logger.error(f"LinkBus: error dispatching to subscriber on '{channel_clean}': {e}")

    def channel_names(self) -> List[str]:
        """Return a list of active channel names."""
        return list(self._subscribers.keys())


# Global singleton instance
link_bus: LinkBus = LinkBus()

