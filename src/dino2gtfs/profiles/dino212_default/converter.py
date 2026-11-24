"""DINO 2.1.2 converter."""
import sqlite3
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from dino2gtfs.transformers.agency import AgencyTransformer
from dino2gtfs.transformers.intf_transformer import Transformer

class Dino212Converter:
    """Run the transformers selected for this profile."""

    transformers: tuple[type[Transformer], ...] = (AgencyTransformer,)

    def convert(self, database: sqlite3.Connection, destination: Path, settings: Mapping[str, Any]) -> None:
        for transformer in self.transformers:
            transformer().transform(database, settings)
