"""Default DINO 2.1.2 profile composition."""
from dataclasses import dataclass, field
from dino2gtfs.profiles.dino212_default.converter import Dino212Converter

@dataclass(frozen=True)
class Dino212DefaultProfile:
    """Strict DINO 2.1.2 profile."""
    name: str = "dino212_default"
    converter: Dino212Converter = field(default_factory=Dino212Converter)
