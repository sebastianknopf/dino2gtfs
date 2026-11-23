"""Conversion use case."""
import sqlite3
from pathlib import Path
from collections.abc import Mapping
from typing import Any
from dino2gtfs.application.interfaces import Profile

def convert_archive(profile: Profile, settings: Mapping[str, Any],
                    database: sqlite3.Connection, destination: Path) -> None:
    """Delegate conversion of imported data to the selected profile."""
    profile.converter.convert(database, destination, settings)
