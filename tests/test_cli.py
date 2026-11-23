from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

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
