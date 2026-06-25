from __future__ import annotations

from pytest import MonkeyPatch

from cost_average_out.config import NotificationSettings
from cost_average_out.notifications import (
    NoneNotificationProvider,
    NotificationError,
    WebhookNotificationProvider,
    create_notification_provider,
)


def test_none_provider_is_noop() -> None:
    provider = create_notification_provider(NotificationSettings(provider="none"))

    assert isinstance(provider, NoneNotificationProvider)


def test_webhook_provider_requires_env_url(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("COST_AVERAGE_OUT_NOTIFICATION_WEBHOOK_URL", raising=False)

    try:
        create_notification_provider(NotificationSettings(provider="webhook"))
    except NotificationError as exc:
        assert "WEBHOOK_URL" in str(exc)
    else:
        raise AssertionError("expected NotificationError")


def test_webhook_provider_can_be_constructed(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(
        "COST_AVERAGE_OUT_NOTIFICATION_WEBHOOK_URL",
        "https://example.test/webhook",
    )

    provider = create_notification_provider(NotificationSettings(provider="webhook"))

    assert isinstance(provider, WebhookNotificationProvider)
