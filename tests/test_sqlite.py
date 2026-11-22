from pathlib import Path
from dino2gtfs.infrastructure.sqlite import temporary_database

def test_database_cleanup():
    with temporary_database() as connection:
        path = Path(connection.execute("PRAGMA database_list").fetchone()[2])
        assert path.exists() and path.suffix == ".db3"
    
    assert not path.exists()
