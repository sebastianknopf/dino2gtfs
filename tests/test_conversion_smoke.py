from zipfile import ZipFile
from dino2gtfs.cli import main

def test_end_to_end_refuses_incomplete_gtfs(tmp_path):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    config = tmp_path / "config.yaml"
    config.write_text("input:\n  encoding: utf-8\n")
    with ZipFile(source, "w") as archive:
        archive.writestr("stop.din", "STOP_NR;STOP_NAME\n1;Test\n")
    assert main(["convert", "--profile", "dino212_default", "--config", str(config), "--input", str(source), "--output", str(destination)]) == 1
    assert not destination.exists()
