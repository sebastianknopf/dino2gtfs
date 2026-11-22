"""YAML configuration loading."""
from pathlib import Path
from typing import Any
import yaml
from dino2gtfs.domain.errors import ConfigurationError

def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"Configuration not found: {path}")
    
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Invalid YAML configuration: {exc}") from exc
    
    if not isinstance(data, dict):
        raise ConfigurationError("Configuration root must be a mapping")
    
    return data
