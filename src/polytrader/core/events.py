"""A minimal in-process event bus.

This is intentionally small: ingestion modules (contracts, congress,
polymarket scanner) publish domain events; the signal matcher and any
future subscriber (alerts, dashboards) subscribe. No external broker --
single-process CLI/service for this build. Swap for a real queue
(e.g. Redis streams) if/when multiple processes need to share events.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class DomainEvent:
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[DomainEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: DomainEvent) -> None:
        for handler in self._handlers.get(event.event_type, []):
            handler(event)


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
