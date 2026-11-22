"""Initial DINO 2.1.2 streaming importer into generic staging rows."""
import io
import json
import logging
import sqlite3
from pathlib import Path
from collections.abc import Mapping
from typing import Any
from zipfile import ZipFile, BadZipFile
from importlib.resources import files
from dino2gtfs.domain.errors import InputError
from dino2gtfs.infrastructure.dinfile import read_din
from dino2gtfs.infrastructure.zipinput import din_members

LOGGER = logging.getLogger(__name__)

class Dino212Importer:
    """Stage source rows without claiming to implement semantic DINO mapping."""

    def import_archive(self, source: Path, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        if not source.is_file():
            raise InputError(f"Input archive not found: {source}")
        
        sql = files("dino2gtfs.profiles.dino212_default").joinpath("schema.sql").read_text()
        database.executescript(sql)
        
        input_settings = settings.get("input", {})
        encoding = input_settings.get("encoding", "cp1252")
        delimiter = input_settings.get("delimiter", ";")

        LOGGER.info("starting import of DINO archive %s", source)
        LOGGER.debug("encoding: %s, delimiter: %s", encoding, delimiter)

        try:
            with ZipFile(source) as archive:
                for member in din_members(archive):
                    LOGGER.info("reading file %s", member.filename)
                    
                    with archive.open(member) as binary:
                        with io.TextIOWrapper(binary, encoding=encoding, newline="") as stream:
                            batch: list[tuple[str, int, str]] = []
                            for row_number, row in enumerate(read_din(stream, delimiter), 2):
                                batch.append((member.filename, row_number, json.dumps(row)))
                                if len(batch) >= 500:
                                    database.executemany("INSERT INTO source_rows VALUES (?, ?, ?)", batch)
                                    batch.clear()
                            
                            if batch:
                                database.executemany("INSERT INTO source_rows VALUES (?, ?, ?)", batch)
                
                database.commit()
        except (BadZipFile, UnicodeError, OSError) as exc:
            raise InputError(f"unable to read DINO archive: {exc}") from exc
        
        LOGGER.info("completed import of DINO archive")
