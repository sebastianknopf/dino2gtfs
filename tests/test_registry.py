import pytest
from dino2gtfs.profiles.registry import get_profile, list_profiles
from dino2gtfs.domain.errors import ConfigurationError

def test_registry():
    assert "dino212_default" in list_profiles()
    assert get_profile("dino212_default").name == "dino212_default"
    with pytest.raises(ConfigurationError):
        get_profile("missing")
