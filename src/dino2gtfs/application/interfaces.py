"""Profile contracts, defined independently of infrastructure."""
from pathlib import Path
from typing import Any, Protocol
from collections.abc import Mapping
import sqlite3

class Importer(Protocol):
    """Import DINO ZIP records into a profile-owned SQLite schema."""
    def import_archive(self, source: Path, database: sqlite3.Connection,
                       settings: Mapping[str, Any]) -> None: ...

class Converter(Protocol):
    """Convert imported source data into a validated GTFS ZIP."""
    def convert(self, database: sqlite3.Connection, destination: Path,
                settings: Mapping[str, Any]) -> None: ...

class Profile(Protocol):
    """A named DINO dialect with importer and converter."""
    name: str
    importer: Importer
    converter: Converter
