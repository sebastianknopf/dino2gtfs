"""DINO 2.1.2 converter."""
import logging
import sqlite3
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from dino2gtfs.transformers.agency import AgencyTransformer
from dino2gtfs.transformers.base_transformer import TransformerBase
from dino2gtfs.transformers.stops import StopsTransformer

LOGGER = logging.getLogger(__name__)

class Dino212Converter:
    """Run the transformers selected for this profile."""

    transformers: tuple[type[TransformerBase], ...] = (AgencyTransformer, StopsTransformer)

    def convert(self, database: sqlite3.Connection, destination: Path, settings: Mapping[str, Any]) -> None:
        LOGGER.info("starting transformations with DINO 2.1.2 profile")
        
        for transformer in self.transformers:
            transformer().transform(database, settings)
