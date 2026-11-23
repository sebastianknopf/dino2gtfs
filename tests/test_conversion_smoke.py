from zipfile import ZipFile
from contextlib import closing
import sqlite3
from dino2gtfs.cli import main

def test_end_to_end_exports_empty_gtfs_feed(tmp_path):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    config = tmp_path / "config.yaml"
    config.write_text("input:\n  encoding: utf-8\n")
    with ZipFile(source, "w") as archive:
        archive.writestr("stop.din", "STOP_NR;STOP_NAME\n1;Test\n")
    assert main(["convert", "--profile", "dino212_default", "--config", str(config), "--input", str(source), "--output", str(destination)]) == 0
    with ZipFile(destination) as archive:
        assert archive.read("stops.txt").count(b"\n") == 1

def test_default_conversion_retains_import_for_debugging(tmp_path):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    config = tmp_path / "config.yaml"
    database = tmp_path / "debug.db3"
    config.write_text("input:\n  encoding: utf-8\n")
    with ZipFile(source, "w") as archive:
        archive.writestr("stop.din", "STOP_NR;STOP_NAME\n1;Test\n")
    assert main(["convert", "--profile", "dino212_default", "--config", str(config),
                 "--input", str(source), "--output", str(destination), "--database", str(database)]) == 0
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT STOP_NR, STOP_NAME FROM dino_stop").fetchall() == [("1", "Test")]
        assert connection.execute("SELECT count(*) FROM gtfs_stops").fetchone() == (0,)
    assert destination.exists()
