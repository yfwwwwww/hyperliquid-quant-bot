"""Push notifications: Telegram, Server酱 (WeChat), WeCom group bot, generic webhook.

Secrets are read from environment variables (never the config file):
  HYPERBOT_TELEGRAM_TOKEN, HYPERBOT_TELEGRAM_CHAT_ID
  HYPERBOT_SERVERCHAN_KEY        (Server酱 SendKey -> WeChat)
  HYPERBOT_WECHAT_WEBHOOK        (企业微信群机器人 webhook)
  HYPERBOT_WEBHOOK_URL           (generic JSON webhook)
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

log = logging.getLogger("hyperbot.notify")


class Notifier:
    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        webhook_url: Optional[str] = None,
        serverchan_key: Optional[str] = None,
        wechat_webhook: Optional[str] = None,
    ):
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.webhook_url = webhook_url
        self.serverchan_key = serverchan_key
        self.wechat_webhook = wechat_webhook
        self.enabled = bool(
            (telegram_token and telegram_chat_id)
            or webhook_url
            or serverchan_key
            or wechat_webhook
        )

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        if self.telegram_token and self.telegram_chat_id:
            self._post_json(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                {"chat_id": self.telegram_chat_id, "text": text, "parse_mode": "HTML"},
            )
        if self.serverchan_key:
            # Server酱 Turbo: pushes to WeChat via the 服务号
            self._post_form(
                f"https://sctapi.ftqq.com/{self.serverchan_key}.send",
                {"title": "hyperbot", "desp": text},
            )
        if self.wechat_webhook:
            self._post_json(
                self.wechat_webhook,
                {"msgtype": "text", "text": {"content": text}},
            )
        if self.webhook_url:
            self._post_json(self.webhook_url, {"text": text})

    # ------------------------------------------------------------------ http
    @staticmethod
    def _do(req: urllib.request.Request) -> None:
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                body = ""
            log.warning("notification HTTP %s: %s", exc.code, body)
        except Exception:
            log.warning("notification send failed", exc_info=True)

    def _post_json(self, url: str, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        self._do(req)

    def _post_form(self, url: str, payload: dict) -> None:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        self._do(req)


def make_notifier(cfg) -> Notifier:
    if not cfg.notify_enabled:
        return Notifier()
    return Notifier(
        telegram_token=cfg.notify_telegram_token,
        telegram_chat_id=cfg.notify_telegram_chat_id,
        webhook_url=cfg.notify_webhook_url,
        serverchan_key=cfg.notify_serverchan_key,
        wechat_webhook=cfg.notify_wechat_webhook,
    )
