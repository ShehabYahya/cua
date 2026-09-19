from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class RuntimeEvent:
    """Structured event emitted by the resident Porter runtime.

    The GUI layer should react to `kind` and structured `data` instead of
    parsing CLI/progress strings. `message` is intentionally still present for
    logs, accessibility, and early UI prototypes.
    """

    kind: str
    message: str = ""
    command_id: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "command_id": self.command_id,
            "data": dict(self.data),
            "timestamp": self.timestamp,
        }


EventSink = Callable[[RuntimeEvent], None]


class RuntimeEventBus:
    """Small synchronous fan-out bus.

    PorterRuntime runs on one asyncio worker loop. GUI bridges can subscribe
    with a callback that forwards events onto Qt's main thread using a queued
    signal. A broken observer must never be allowed to break computer control.
    """

    def __init__(self) -> None:
        self._sinks: list[EventSink] = []

    def subscribe(self, sink: EventSink) -> Callable[[], None]:
        if sink not in self._sinks:
            self._sinks.append(sink)

        def unsubscribe() -> None:
            self.unsubscribe(sink)

        return unsubscribe

    def unsubscribe(self, sink: EventSink) -> None:
        try:
            self._sinks.remove(sink)
        except ValueError:
            pass

    def emit(self, event: RuntimeEvent) -> None:
        for sink in tuple(self._sinks):
            try:
                sink(event)
            except Exception:
                # Diagnostics/UI listeners are observational only.
                continue
