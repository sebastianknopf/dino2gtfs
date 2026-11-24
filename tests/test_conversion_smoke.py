from zipfile import ZipFile
from contextlib import closing
import sqlite3
from pathlib import Path
from dino2gtfs.cli import main

def test_end_to_end_exports_agency(tmp_path):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    config = Path(__file__).resolve().parents[1] / "config" / "default.yaml"
    with ZipFile(source, "w") as archive:
        archive.writestr("stop.din", "STOP_NR;STOP_NAME\n1;Test\n")
        archive.writestr("operator.din", "OP_CODE;OP_LONG_NAME\n1;Operator\n")
    assert main(["convert", "--profile", "dino212_default", "--config", str(config), "--input", str(source), "--output", str(destination)]) == 0
    with ZipFile(destination) as archive:
        assert b"agency:1,Operator,https://example.org,Europe/Berlin" in archive.read("agency.txt")
        assert archive.read("stops.txt").count(b"\n") == 1

def test_default_conversion_retains_import_for_debugging(tmp_path):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    config = tmp_path / "config.yaml"
    database = tmp_path / "debug.db3"
    config.write_text(
        "input:\n  encoding: utf-8\n"
        "defaults:\n  agency:\n    url: https://example.org\n    timezone: UTC\n"
        "ids:\n  agency:\n    mode: template\n    template: 'operator-{operator.OP_CODE}'\n"
    )
    with ZipFile(source, "w") as archive:
        archive.writestr("stop.din", "STOP_NR;STOP_NAME\n1;Test\n")
        archive.writestr("operator.din", "OP_CODE;OP_LONG_NAME\n1;Operator\n")
    assert main(["convert", "--profile", "dino212_default", "--config", str(config),
                 "--input", str(source), "--output", str(destination), "--database", str(database)]) == 0
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT STOP_NR, STOP_NAME FROM dino_stop").fetchall() == [("1", "Test")]
        assert connection.execute("SELECT count(*) FROM gtfs_stops").fetchone() == (0,)
        assert connection.execute("SELECT agency_id FROM gtfs_agency").fetchall() == [("operator-1",)]
    assert destination.exists()
