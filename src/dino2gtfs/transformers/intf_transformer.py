"""Transformer contract."""
import sqlite3
from collections.abc import Mapping
from typing import Any, Protocol


class Transformer(Protocol):
    """Transform imported data into one GTFS object table."""

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None: ...
