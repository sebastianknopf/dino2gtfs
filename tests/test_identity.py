import pytest
from dino2gtfs.domain.identity import IdentityRegistry
from dino2gtfs.domain.errors import CollisionError

def test_collision():
    registry = IdentityRegistry()
    registry.register("stop", "1", "row1")
    registry.register("stop", "1", "row1")
    with pytest.raises(CollisionError):
        registry.register("stop", "1", "row2")
