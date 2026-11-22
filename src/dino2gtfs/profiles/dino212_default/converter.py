"""DINO 2.1.2 converter extension point."""
import sqlite3
from pathlib import Path
from collections.abc import Mapping
from typing import Any
from dino2gtfs.domain.errors import ConversionNotImplementedError

class Dino212Converter:
    """Deliberately refuse incomplete GTFS generation."""

    def convert(self, database: sqlite3.Connection, destination: Path, settings: Mapping[str, Any]) -> None:
        raise ConversionNotImplementedError(
            "DINO 2.1.2 to GTFS field mapping is not implemented in this scaffold"
        )
