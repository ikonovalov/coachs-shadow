from dataclasses import dataclass


@dataclass(frozen=True)
class OrderLine:
    user_name: str
    kind: str
    label: str
    frame: str | None = None
    shoe_eu: int | None = None
    pedal_type: str | None = None
    helmet_size: str | None = None
