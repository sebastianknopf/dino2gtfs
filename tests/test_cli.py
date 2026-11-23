import os
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from dino2gtfs.cli import main

def test_profiles(capsys):
    assert main(["profiles"]) == 0
    assert "dino212_default" in capsys.readouterr().out

def test_convert_imports_before_calling_profile(tmp_path, monkeypatch):
    source = tmp_path / "in.zip"
    destination = tmp_path / "out.zip"
    config = tmp_path / "config.yaml"
    config.write_text("input:\n  encoding: utf-8\n")
    with ZipFile(source, "w") as archive:
        archive.writestr("stop.din", "ID;NAME\n1;Test\n")

    rows = []
    converter = Mock()
    converter.convert.side_effect = lambda db, destination, settings: rows.extend(
        db.execute("SELECT ID, NAME FROM dino_stop").fetchall()
    )
    monkeypatch.setattr("dino2gtfs.cli.get_profile", lambda name: SimpleNamespace(name=name, converter=converter))
    assert main(["convert", "--profile", "another_profile", "--config", str(config),
                 "--input", str(source), "--output", str(destination)]) == 0
    assert rows == [("1", "Test")]
    assert converter.convert.call_count == 1
    _, actual_destination, settings = converter.convert.call_args.args
    assert actual_destination == destination
    assert settings == {"input": {"encoding": "utf-8"}}

def test_convert_does_not_run_converter_on_import_failure(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("input:\n  encoding: utf-8\n")
    converter = Mock()
    monkeypatch.setattr("dino2gtfs.cli.get_profile", lambda name: SimpleNamespace(name=name, converter=converter))
    assert main(["convert", "--profile", "another_profile", "--config", str(config),
                 "--input", str(tmp_path / "missing.zip"), "--output", str(tmp_path / "out.zip")]) == 3
    converter.convert.assert_not_called()


@pytest.mark.parametrize("fail", [False, True])
def test_persistent_database_replaces_existing_and_preserves_import(tmp_path, monkeypatch, fail):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    config = tmp_path / "config.yaml"
    database_path = tmp_path / "debug.db3"
    config.write_text("input:\n  encoding: utf-8\n")
    with ZipFile(source, "w") as archive:
        archive.writestr("stop.din", "ID;NAME\n1;Test\n")
    with closing(sqlite3.connect(database_path)) as old_db:
        old_db.execute("CREATE TABLE old_data (value TEXT)")
        old_db.commit()

    def convert(db, destination, settings):
        db.execute("INSERT INTO gtfs_agency (agency_name, agency_url, agency_timezone) VALUES ('Test', 'https://example.com', 'UTC')")
        if fail:
            raise RuntimeError("conversion failed")

    converter = Mock()
    converter.convert.side_effect = convert
    monkeypatch.setattr("dino2gtfs.cli.get_profile", lambda name: SimpleNamespace(name=name, converter=converter))
    result = main(["convert", "--profile", "another_profile", "--config", str(config),
                   "--input", str(source), "--output", str(destination), "--database", str(database_path)])
    assert result == (1 if fail else 0)
    with closing(sqlite3.connect(database_path)) as db:
        assert db.execute("SELECT ID, NAME FROM dino_stop").fetchall() == [("1", "Test")]
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'old_data'").fetchall() == []
        assert db.execute("SELECT agency_name FROM gtfs_agency").fetchall() == ([] if fail else [("Test",)])


@pytest.mark.parametrize("conflict", ["input", "output", "config", "hardlink"])
def test_database_path_cannot_replace_input_output_or_config(tmp_path, monkeypatch, conflict):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    config = tmp_path / "config.yaml"
    config.write_text("input:\n  encoding: utf-8\n")
    source.write_bytes(b"input")
    destination.write_bytes(b"output")
    database_path = {"input": source, "output": destination, "config": config}.get(conflict, tmp_path / "hardlink.db3")
    if conflict == "hardlink":
        try:
            os.link(source, database_path)
        except OSError as exc:
            pytest.skip(f"Hard links unavailable: {exc}")

    monkeypatch.setattr("dino2gtfs.cli.get_profile", lambda name: SimpleNamespace(name=name, converter=Mock()))
    assert main(["convert", "--profile", "another_profile", "--config", str(config),
                 "--input", str(source), "--output", str(destination), "--database", str(database_path)]) == 2
    assert source.read_bytes() == b"input"
    assert destination.read_bytes() == b"output"
    assert config.read_text() == "input:\n  encoding: utf-8\n"


def test_database_replacement_refuses_sqlite_sidecars(tmp_path, monkeypatch):
    database_path = tmp_path / "debug.db3"
    database_path.write_bytes(b"existing")
    (tmp_path / "debug.db3-wal").write_bytes(b"sidecar")
    config = tmp_path / "config.yaml"
    config.write_text("input:\n  encoding: utf-8\n")
    monkeypatch.setattr("dino2gtfs.cli.get_profile", lambda name: SimpleNamespace(name=name, converter=Mock()))
    assert main(["convert", "--profile", "another_profile", "--config", str(config),
                 "--input", str(tmp_path / "in.zip"), "--output", str(tmp_path / "out.zip"),
                 "--database", str(database_path)]) == 2
    assert database_path.read_bytes() == b"existing"
