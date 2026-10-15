from __future__ import annotations

from .base import Deliverer, DeliveryError
from .smtp_email import EmailDeliverer
from .teams import TeamsDeliverer

_CHANNELS = {"email": EmailDeliverer, "teams": TeamsDeliverer}


def enabled_deliverers(settings: dict) -> list[Deliverer]:
    cfg = (settings.get("delivery") or {})
    return [cls(cfg[name]) for name, cls in _CHANNELS.items() if (cfg.get(name) or {}).get("enabled")]


__all__ = ["enabled_deliverers", "Deliverer", "DeliveryError"]
