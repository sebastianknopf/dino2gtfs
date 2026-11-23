"""Profile-independent DINO ZIP importer."""
import io
import logging
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from dino2gtfs.domain.errors import InputError
from dino2gtfs.infrastructure.dinfile import read_din_with_headers
from dino2gtfs.infrastructure.zipinput import din_members

LOGGER = logging.getLogger(__name__)


def _quote_identifier(name: str) -> str:
    if "\x00" in name:
        raise InputError("DINO table and column names cannot contain NUL characters")
    
    return '"' + name.replace('"', '""') + '"'


class DinoImporter:
    """Import each DINO file into a table derived from its filename and headers."""

    def import_archive(self, source: Path, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        if not source.is_file():
            raise InputError(f"Input archive not found: {source}")

        input_settings = settings.get("input", {})
        encoding = input_settings.get("encoding", "cp1252")
        delimiter = input_settings.get("delimiter", ";")
        
        LOGGER.info("starting import of DINO archive %s", source)
        LOGGER.debug("encoding: %s, delimiter: %s", encoding, delimiter)

        try:
            with ZipFile(source) as archive:
                database.execute("SAVEPOINT dino_import")
                try:
                    for member in din_members(archive):
                        LOGGER.info("reading file %s", member.filename)
                        
                        table = _quote_identifier("dino_" + member.filename[:-4])
                        with archive.open(member) as binary:
                            with io.TextIOWrapper(binary, encoding=encoding, newline="") as stream:
                                headers, rows = read_din_with_headers(stream, delimiter)
                                
                                # SQLite compares identifiers case-insensitively for ASCII letters.
                                sqlite_names = [name.translate(str.maketrans(
                                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
                                )) for name in headers]
                                
                                if len(set(sqlite_names)) != len(headers):
                                    raise InputError(f"DINO file contains duplicate SQLite column names: {member.filename}")
                                
                                columns = [_quote_identifier(name) for name in headers]
                                database.execute(
                                    f"CREATE TABLE {table} ({', '.join(column + ' TEXT' for column in columns)})"
                                )
                                
                                insert = f"INSERT INTO {table} VALUES ({', '.join('?' for _ in columns)})"
                                batch: list[tuple[str, ...]] = []
                                for row in rows:
                                    batch.append(tuple(row[header] for header in headers))
                                    if len(batch) >= 500:
                                        database.executemany(insert, batch)
                                        batch.clear()
                                
                                if batch:
                                    database.executemany(insert, batch)
                    
                    database.execute("RELEASE SAVEPOINT dino_import")
                except BaseException:
                    database.execute("ROLLBACK TO SAVEPOINT dino_import")
                    database.execute("RELEASE SAVEPOINT dino_import")
                    
                    raise
        except (BadZipFile, UnicodeError, OSError) as exc:
            raise InputError(f"unable to read DINO archive: {exc}") from exc

        LOGGER.info("completed import of DINO archive")
