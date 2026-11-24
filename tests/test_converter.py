import sqlite3
from pathlib import Path
from unittest.mock import Mock

from dino2gtfs.profiles.dino212_default.converter import Dino212Converter
from dino2gtfs.transformers.agency import AgencyTransformer

def test_converter_runs_selected_transformers(tmp_path: Path):
    transform = Mock()

    class CustomAgencyTransformer(AgencyTransformer):
        def transform(self, database, settings):
            transform(database, settings)

    converter = Dino212Converter()
    converter.transformers = (CustomAgencyTransformer,)
    settings = {"agency": {"timezone": "UTC"}}
    with sqlite3.connect(":memory:") as db:
        converter.convert(db, tmp_path / "out.zip", settings)
        transform.assert_called_once_with(db, settings)
