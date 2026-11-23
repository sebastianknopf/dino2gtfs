"""Temporary SQLite lifecycle."""
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

@contextmanager
def temporary_database() -> Iterator[sqlite3.Connection]:
    """Provide a temporary on-disk database removed on exit."""
    with TemporaryDirectory(prefix="dino2gtfs-") as directory:
        path = Path(directory) / "staging.db3"
        connection = sqlite3.connect(path)
        
        try:
            schema = files("dino2gtfs.infrastructure").joinpath("gtfs_static_schema.sql").read_text(encoding="utf-8")
            connection.executescript(schema)
            
            yield connection
        finally:
            connection.close()
