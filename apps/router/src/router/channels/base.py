from __future__ import annotations

from abc import ABC, abstractmethod

from router.models import CaseAlert


class ChannelAdapter(ABC):
    @abstractmethod
    async def is_available(self) -> bool: ...

    @abstractmethod
    async def send(self, alert: CaseAlert) -> str | None:
        """Send alert. Returns an opaque message-ID string on success, None on failure."""

    @abstractmethod
    async def send_ack_confirmation(self, alert: CaseAlert, message_id: str) -> None:
        """Send a follow-up message confirming the alert was acknowledged."""
