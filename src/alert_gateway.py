"""Alert gateway for AAMAS.

Dispatches behavior events (stimming start/resolved, avoidance start/resolved)
to a local/remote webhook endpoint and optionally via Twilio SMS.
"""

import json
import os
import queue
import threading
import time
import uuid

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.logging_config import get_logger

logger = get_logger(__name__)


class AlertGateway:
    """Asynchronous alert dispatcher for AAMAS edge events.

    Supports both Webhook POST and Twilio SMS notifications.
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        enable_mobile: bool | None = None,
        twilio_account_sid: str | None = None,
        twilio_auth_token: str | None = None,
        twilio_from_number: str | None = None,
        target_mobile: str | None = None,
    ):
        self.webhook_url = webhook_url or os.environ.get(
            "WEBHOOK_URL", "http://localhost:5001/webhook"
        )
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        self.session.mount("http://", HTTPAdapter(max_retries=retries))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

        if enable_mobile is None:
            enable_mobile_str = os.environ.get("ENABLE_MOBILE_NOTIFICATIONS", "false").lower()
            self.enable_mobile = enable_mobile_str in ("true", "1", "yes")
        else:
            self.enable_mobile = enable_mobile

        self.account_sid = twilio_account_sid or os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
        self.auth_token = twilio_auth_token or os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
        self.from_number = twilio_from_number or os.environ.get("TWILIO_FROM_NUMBER", "").strip()
        self.target_mobile = target_mobile or os.environ.get("TARGET_CAREGIVER_MOBILE", "").strip()

        self.twilio_client = None
        if self.enable_mobile:
            if self.account_sid and self.auth_token and self.from_number and self.target_mobile:
                try:
                    from twilio.rest import Client

                    self.twilio_client = Client(self.account_sid, self.auth_token)
                    logger.info("Twilio SMS alerting enabled.")
                except Exception as e:
                    logger.warning("Failed to initialize Twilio client: %s", e)
                    self.enable_mobile = False
            else:
                logger.warning(
                    "[Gateway Warning] Twilio environment variables are missing or unconfigured. Falling back gracefully."
                )
                self.enable_mobile = False

        self.queue: queue.Queue = queue.Queue()
        self._shutdown_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()

        logger.debug(
            "AlertGateway initialized (webhook: %s, sms: %s)",
            self.webhook_url,
            self.enable_mobile,
        )

    def send_alert(
        self,
        event_status: str,
        frequency: float,
        mean_velocity: float,
        duration: float,
        event_type: str = "stimming",
        detail: str | None = None,
    ):
        """Enqueue an alert to be dispatched asynchronously."""
        payload = {
            "event_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "event_type": event_type,
            "status": event_status,
            "detail": detail,
            "metadata": {
                "frequency_hz": round(frequency, 2),
                "avg_velocity": round(mean_velocity, 4),
                "duration_seconds": round(duration, 2),
            },
        }
        self.queue.put(payload)

    def _process_queue(self):
        while not self._shutdown_event.is_set():
            try:
                payload = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue

            self._dispatch_post(payload)
            if self.enable_mobile:
                self._dispatch_sms(payload)
            self.queue.task_done()

    def _dispatch_post(self, payload: dict):
        if not self.webhook_url:
            return
        try:
            resp = self.session.post(
                self.webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=3.0,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "Webhook responded with HTTP %s: %s", resp.status_code, resp.text
                )
        except Exception as e:
            logger.error("Failed to post alert to %s: %s", self.webhook_url, e)

    def _dispatch_sms(self, payload: dict):
        if not self.enable_mobile or not self.twilio_client:
            return
        status = payload.get("status", "alert")
        event_type = payload.get("event_type", "behavior")
        detail = payload.get("detail", "")
        detail_str = f" ({detail})" if detail else ""
        meta = payload.get("metadata", {})
        dur = meta.get("duration_seconds", 0)
        body = f"[AAMAS Alert] {status.upper()}: {event_type}{detail_str} (duration: {dur}s)"
        try:
            self.twilio_client.messages.create(
                body=body,
                from_=self.from_number,
                to=self.target_mobile,
            )
            logger.info("SMS alert sent to %s", self.target_mobile)
        except Exception as e:
            logger.error("Failed to send Twilio SMS: %s", e)

    def shutdown(self):
        """Wait for queued items and stop background worker."""
        self._shutdown_event.set()
        if self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)
        self.session.close()
