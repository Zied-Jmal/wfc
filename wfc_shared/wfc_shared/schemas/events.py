"""
wfc_shared.schemas.events - Fire event schemas published by ground sensors.
FireEvent wraps a FirePayload and carries lifecycle event type.
Topic : wfc/events/fire  |  QoS 1
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# Constrained type aliases
Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
"""Fire intensity severity rating."""
FireEventType = Literal[
    "FIRE_DETECTED",
    "FIRE_SUPPRESSED",
    "FIRE_CONTAINED",
    "FIRE_INTENSITY_UPDATE",
    "FIRE_REKINDLED",
    "FIRE_VERIFIED",
    "FIRE_PERIMETER_UPDATE",
]
"""Fire lifecycle event types from wfc_shared.enums.events."""


class FirePayload(BaseModel):
    """Payload carried by every fire lifecycle event.

    Back-compat: old sensors that publish "location" instead of "zone" are
    handled by the model_validator - location is copied to zone automatically.

    Args:
        fire_id: Globally unique fire identifier (UUID).
        zone: Zone label (e.g. "zone_alpha") - matches NodeRecord.zone.
        severity: LOW | MEDIUM | HIGH | CRITICAL.
        sensor_id: Node_id of the detecting sensor.
        location_coords: (lat_deg, lon_deg) WGS-84 for distance-based dispatch.
    """

    fire_id: str
    """Globally unique fire identifier (UUID)."""
    zone: str
    """Zone label (e.g. "zone_alpha") - matches NodeRecord.zone."""
    severity: Severity
    """LOW | MEDIUM | HIGH | CRITICAL."""
    sensor_id: str
    """Node_id of the detecting sensor."""
    location_coords: tuple[float, float] | None = None
    """(lat_deg, lon_deg) WGS-84 for distance-based dispatch."""

    @model_validator(mode="before")
    @classmethod
    def _copy_location_to_zone(cls, data: Any) -> dict[str, Any]:
        """Back-compat: copy ``location`` to ``zone`` for old sensor payloads.

        Old sensors publish ``location`` instead of ``zone``.
        Runs before field validation and copies the value.

        Args:
            data: Raw input dict before Pydantic validation.

        Returns:
            Modified data dict with ``zone`` populated.
        """
        if isinstance(data, dict) and "zone" not in data and "location" in data:
            data = dict(data)  # pyright: ignore[reportUnknownArgumentType,reportUnknownVariableType]
            data["zone"] = data["location"]  # pyright: ignore[reportUnknownMemberType,reportIndexIssue]
        return data  # pyright: ignore[reportUnknownVariableType]

    @property
    def location(self) -> str:
        """Read-only alias for zone."""
        return self.zone


class FireEvent(BaseModel):
    """Wrapper for a fire lifecycle event published over MQTT by a ground sensor.

    Args:
        event_id: UUID unique per event.
        event_type: One of the FireEventType constants.
        timestamp: UNIX epoch seconds (UTC).
        source: Node_id of the publishing sensor.
        payload: FirePayload with fire details.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """UUID unique per event."""
    event_type: FireEventType
    """One of the FireEventType constants."""
    timestamp: float = Field(default_factory=time.time)
    """UNIX epoch seconds (UTC)."""
    source: str
    """Node_id of the publishing sensor."""
    payload: FirePayload
    """FirePayload with fire details."""
