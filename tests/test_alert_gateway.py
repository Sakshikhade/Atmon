"""Tests for src/alert_gateway.py.

Covers both Webhook and SMS alerting pathways.
"""

import json
import time
from unittest.mock import MagicMock, patch

import requests as req

from src.alert_gateway import AlertGateway


def test_alert_gateway_webhook_post_on_send_alert():
    """send_alert enqueues a payload and the worker POSTs it to the webhook URL."""
    with patch("requests.Session.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        gateway = AlertGateway(webhook_url="http://localhost:5001/webhook")
        gateway.send_alert(
            event_status="stimming_start",
            frequency=4.5,
            mean_velocity=0.08,
            duration=1.5,
        )
        # Give the background worker time to drain the queue.
        time.sleep(0.15)
        gateway.shutdown()

        assert mock_post.called
        _, kwargs = mock_post.call_args
        assert kwargs["timeout"] == 3.0


def test_alert_gateway_payload_shape():
    """The dispatched JSON payload contains the expected top-level keys."""
    with patch("requests.Session.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        gateway = AlertGateway(webhook_url="http://localhost:5001/webhook")
        gateway.send_alert(
            event_status="avoidance_start",
            frequency=3.2,
            mean_velocity=0.05,
            duration=2.0,
            event_type="avoidance",
            detail="ears_covered",
        )
        time.sleep(0.15)
        gateway.shutdown()

        assert mock_post.called
        _, kwargs = mock_post.call_args
        body = json.loads(kwargs["data"])

    assert "event_id" in body
    assert "timestamp" in body
    assert body["event_type"] == "avoidance"
    assert body["status"] == "avoidance_start"
    assert body["detail"] == "ears_covered"
    assert "metadata" in body
    assert body["metadata"]["frequency_hz"] == 3.2
    assert body["metadata"]["avg_velocity"] == 0.05
    assert body["metadata"]["duration_seconds"] == 2.0


def test_alert_gateway_webhook_failure_does_not_raise():
    """A connection error from the webhook endpoint is caught and logged — no crash."""
    with patch("requests.Session.post", side_effect=req.exceptions.ConnectionError("refused")):
        gateway = AlertGateway(webhook_url="http://localhost:5001/webhook")
        # Should not raise.
        gateway.send_alert(
            event_status="stimming_resolved",
            frequency=4.0,
            mean_velocity=0.06,
            duration=3.0,
        )
        time.sleep(0.15)
        gateway.shutdown()


def test_alert_gateway_sms_dispatch_when_enabled():
    """Twilio SMS is dispatched when mobile notifications are enabled with valid credentials."""
    with patch("twilio.rest.Client") as mock_client_cls, patch("requests.Session.post"):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        gateway = AlertGateway(
            webhook_url="http://localhost:5001/webhook",
            enable_mobile=True,
            twilio_account_sid="AC1234567890",
            twilio_auth_token="auth_token_secret",
            twilio_from_number="+15550001",
            target_mobile="+15550002",
        )

        assert gateway.enable_mobile is True
        assert gateway.twilio_client is not None

        gateway.send_alert(
            event_status="stimming_start",
            frequency=4.5,
            mean_velocity=0.08,
            duration=1.5,
            event_type="rhythmic_stimming",
        )
        time.sleep(0.15)
        gateway.shutdown()

        assert mock_client.messages.create.called
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["from_"] == "+15550001"
        assert call_kwargs["to"] == "+15550002"
        assert "STIMMING_START" in call_kwargs["body"]


def test_alert_gateway_sms_graceful_fallback_on_missing_credentials():
    """If enable_mobile is True but credentials are missing, mobile alerting is disabled gracefully."""
    gateway = AlertGateway(
        enable_mobile=True,
        twilio_account_sid="",
        twilio_auth_token="",
    )
    assert gateway.enable_mobile is False
    assert gateway.twilio_client is None
    gateway.shutdown()
