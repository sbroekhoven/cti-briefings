"""Microsoft Teams via a Workflows webhook ("Post to a channel when a webhook request is received").
The classic Office 365 connectors are retired, so this posts an Adaptive Card in the Workflows format."""
from __future__ import annotations

import httpx

from ..config import env
from .base import Deliverer, DeliveryError

COLORS = {"critical": "Attention", "high": "Warning", "medium": "Accent", "low": "Default"}


class TeamsDeliverer(Deliverer):
    name = "teams"

    def send(self, ctx: dict, html: str, text: str, brief_path: str) -> None:
        body: list[dict] = [
            {"type": "TextBlock", "text": f"CTI Brief {ctx['date']}", "weight": "Bolder", "size": "Large"},
            {"type": "TextBlock", "isSubtle": True, "spacing": "None",
             "text": f"{len(ctx['act'])} act, {len(ctx['signal'])} signal, {ctx['stats']['reviewed']} reviewed"},
        ]
        for a in ctx["act"][: int(self.cfg.get("max_act_items", 5))]:
            body.append({"type": "TextBlock", "wrap": True, "spacing": "Medium", "color": COLORS.get(a["severity"], "Default"),
                         "text": f"**[{a['severity'].upper()} / {a['action']}]** [{a['headline']}]({a['links'][0]})"})
            body.append({"type": "TextBlock", "wrap": True, "spacing": "None", "text": a["why_it_matters"]})
        signals = ctx["signal"][: int(self.cfg.get("max_signal_items", 8))]
        if signals:
            body.append({"type": "TextBlock", "text": "Signal", "weight": "Bolder", "spacing": "Large"})
            for s in signals:
                body.append({"type": "TextBlock", "wrap": True, "spacing": "Small",
                             "text": f"- [{s['line']}]({s['item'].link})"})
        vulns = ctx.get("vulns", [])[: int(self.cfg.get("max_vuln_items", 5))]
        if vulns:
            body.append({"type": "TextBlock", "text": f"Vulnerabilities ({len(ctx['vulns'])})", "weight": "Bolder",
                         "spacing": "Large"})
            for v in vulns:
                flag = " **exploited**" if v["exploited"] else ""
                body.append({"type": "TextBlock", "wrap": True, "spacing": "Small",
                             "text": f"- [{v['cve'] or 'link'}]({v['link']}){flag} {v['product']} {v['description']}"})
        self._post(body)

    def send_alert(self, subject: str, text: str) -> None:
        self._post([{"type": "TextBlock", "text": subject, "weight": "Bolder", "size": "Large", "color": "Attention"},
                    {"type": "TextBlock", "text": text, "wrap": True}])

    def _post(self, body: list[dict]) -> None:
        url = env(self.cfg.get("webhook_env", "TEAMS_WEBHOOK_URL"))
        if not url:
            raise DeliveryError(f"{self.cfg.get('webhook_env', 'TEAMS_WEBHOOK_URL')} is not set")
        payload = {"type": "message", "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard", "version": "1.4", "body": body,
                        "msteams": {"width": "Full"}}}]}  # use the full message width instead of the narrow default
        try:
            httpx.post(url, json=payload, timeout=20).raise_for_status()
        except httpx.HTTPError as exc:
            raise DeliveryError(f"Teams webhook failed: {exc}") from exc
