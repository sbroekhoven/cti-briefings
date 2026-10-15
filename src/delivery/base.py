from __future__ import annotations

from abc import ABC, abstractmethod


class DeliveryError(RuntimeError):
    pass


class Deliverer(ABC):
    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @abstractmethod
    def send(self, ctx: dict, html: str, text: str, brief_path: str) -> None: ...

    def send_alert(self, subject: str, text: str) -> None:
        """Short plain notice, e.g. that a run failed. Channels that cannot do this simply skip it."""
