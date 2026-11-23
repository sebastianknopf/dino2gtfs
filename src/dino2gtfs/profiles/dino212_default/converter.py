"""DINO 2.1.2 converter extension point."""
import logging
import sqlite3
from pathlib import Path
from collections.abc import Mapping
from typing import Any

LOGGER = logging.getLogger(__name__)

class Dino212Converter:
    """Placeholder converter which leaves the GTFS tables unchanged."""

    def convert(self, database: sqlite3.Connection, destination: Path, settings: Mapping[str, Any]) -> None:
        LOGGER.warning("DINO 2.1.2 to GTFS field mapping is not implemented; GTFS tables remain empty")
