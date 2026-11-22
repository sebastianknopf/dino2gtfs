from unittest.mock import Mock
import pytest
from dino2gtfs.application.convert import convert_archive

def test_orchestration(tmp_path):
    profile = Mock()
    profile.converter.convert.side_effect = RuntimeError("stub")
    with pytest.raises(RuntimeError, match="stub"):
        convert_archive(profile, {}, tmp_path / "in.zip", tmp_path / "out.zip")
    profile.importer.import_archive.assert_called_once()
    profile.converter.convert.assert_called_once()
