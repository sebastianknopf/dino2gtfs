import sqlite3
import pytest
from pathlib import Path
from dino2gtfs.profiles.dino212_default.converter import Dino212Converter
from dino2gtfs.domain.errors import ConversionNotImplementedError

def test_converter_is_explicitly_unimplemented(tmp_path: Path):
    with sqlite3.connect(":memory:") as db:
        with pytest.raises(ConversionNotImplementedError):
            Dino212Converter().convert(db, tmp_path / "out.zip", {})
    assert not (tmp_path / "out.zip").exists()
