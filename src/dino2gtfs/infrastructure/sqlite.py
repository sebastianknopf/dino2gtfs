"""SQLite lifecycle and GTFS schema initialization."""
import logging
import os
import sqlite3
from contextlib import ExitStack, contextmanager
from collections.abc import Generator
from importlib.resources import files
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from dino2gtfs.domain.errors import ConfigurationError

LOGGER = logging.getLogger(__name__)

def _replace_database(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise ConfigurationError(f"Cannot replace database with an existing SQLite sidecar: {path}{suffix}")

    temporary_path = None
    try:
        with NamedTemporaryFile(prefix=".dino2gtfs-", dir=path.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        
        os.replace(temporary_path, path)
    except OSError as exc:
        raise ConfigurationError(f"Cannot create database at {path}: {exc}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

@contextmanager
def open_database(path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Provide a fresh database, temporary unless a persistent path is supplied."""
    with ExitStack() as stack:
        if path is None:
            directory = stack.enter_context(TemporaryDirectory(prefix="dino2gtfs-"))
            path = Path(directory) / "staging.db3"
        else:
            LOGGER.info("retaining SQLite database at %s", path)
            _replace_database(path)

        connection = sqlite3.connect(path.resolve())
        
        try:
            schema = files("dino2gtfs.infrastructure").joinpath("gtfs_static_schema.sql").read_text(encoding="utf-8")
            connection.executescript(schema)
            
            yield connection
            
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

@contextmanager
def temporary_database() -> Generator[sqlite3.Connection, None, None]:
    """Provide a temporary database removed on exit."""
    with open_database() as connection:
        yield connection
