from pathlib import Path
import pytest
from dino2gtfs.infrastructure.config import load_config
from dino2gtfs.domain.errors import ConfigurationError

def test_config(tmp_path: Path):
    file = tmp_path / "settings.yaml"
    file.write_text("input:\n  encoding: utf-8\n")
    assert load_config(file)["input"]["encoding"] == "utf-8"
    with pytest.raises(ConfigurationError):
        load_config(tmp_path / "missing.yaml")
