from pathlib import Path
import pytest
from dino2gtfs.infrastructure.sqlite import temporary_database

def test_database_cleanup():
    with temporary_database() as connection:
        path = Path(connection.execute("PRAGMA database_list").fetchone()[2])
        assert path.exists() and path.suffix == ".db3"
    
    assert not path.exists()

def test_temporary_database_cleanup_on_failure():
    with pytest.raises(RuntimeError, match="failure"):
        with temporary_database() as connection:
            path = Path(connection.execute("PRAGMA database_list").fetchone()[2])
            raise RuntimeError("failure")
    assert not path.exists()
