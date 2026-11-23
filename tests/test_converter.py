import logging
import sqlite3
from pathlib import Path
from dino2gtfs.profiles.dino212_default.converter import Dino212Converter

def test_converter_logs_missing_mapping_without_failing(tmp_path: Path, caplog):
    with sqlite3.connect(":memory:") as db:
        with caplog.at_level(logging.WARNING, logger="dino2gtfs.profiles.dino212_default.converter"):
            Dino212Converter().convert(db, tmp_path / "out.zip", {})
    assert "field mapping is not implemented" in caplog.text
    assert not (tmp_path / "out.zip").exists()
