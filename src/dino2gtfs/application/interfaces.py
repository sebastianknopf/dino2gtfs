"""Profile contracts, defined independently of infrastructure."""
from pathlib import Path
from typing import Any, Protocol
from collections.abc import Mapping
import sqlite3

class Converter(Protocol):
    """Convert imported source data into GTFS tables."""
    def convert(self, database: sqlite3.Connection, destination: Path,
                settings: Mapping[str, Any]) -> None: ...

class Profile(Protocol):
    """A named DINO conversion profile."""
    name: str
    converter: Converter
