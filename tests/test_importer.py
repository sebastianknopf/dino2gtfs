from zipfile import ZipFile
from dino2gtfs.profiles.dino212_default.importer import Dino212Importer
from dino2gtfs.infrastructure.sqlite import temporary_database

def test_importer(tmp_path):
    archive_path = tmp_path / "dino.zip"
    
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("stop.din", "STOP_NR;STOP_NAME\n1;Test\n")
   
    with temporary_database() as db:
        Dino212Importer().import_archive(archive_path, db, {"input": {"encoding": "utf-8"}})
        assert db.execute("SELECT count(*) FROM source_rows").fetchone()[0] == 1
