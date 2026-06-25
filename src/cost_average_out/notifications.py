"""Notification provider contracts and implementations."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from cost_average_out.config import NotificationSettings

WEBHOOK_URL_ENV = "COST_AVERAGE_OUT_NOTIFICATION_WEBHOOK_URL"


class NotificationError(RuntimeError):
    """Raised when a configured notification cannot be delivered."""


class NotificationKind(StrEnum):
    SUCCESS = "success"
    SKIP = "skip"
    BLOCK = "block"
    FAILURE = "failure"


@dataclass(frozen=True)
class NotificationMessage:
    kind: NotificationKind
    title: str
    body: str


class NotificationProvider(Protocol):
    def send(self, message: NotificationMessage) -> None: ...


class NoneNotificationProvider:
    def send(self, message: NotificationMessage) -> None:
        return None


class WebhookNotificationProvider:
    def __init__(self, url: str, timeout_seconds: float = 10.0) -> None:
        if not url.strip():
            raise NotificationError(f"{WEBHOOK_URL_ENV} is required")
        self._url = url
        self._timeout_seconds = timeout_seconds

    def send(self, message: NotificationMessage) -> None:
        payload = json.dumps(
            {
                "kind": message.kind.value,
                "title": message.title,
                "body": message.body,
            }
        ).encode("utf-8")
        request = Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                status = int(response.status)
        except URLError as exc:
            raise NotificationError(f"webhook notification failed: {exc}") from exc
        if status >= 400:
            raise NotificationError(f"webhook notification failed with HTTP {status}")


def create_notification_provider(
    settings: NotificationSettings,
) -> NotificationProvider:
    if settings.provider == "none":
        return NoneNotificationProvider()
    return WebhookNotificationProvider(os.getenv(WEBHOOK_URL_ENV, ""))
