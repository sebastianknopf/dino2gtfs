"""Deterministic identity checks."""
from dino2gtfs.domain.errors import CollisionError

class IdentityRegistry:
    """Reject duplicate identities for distinct source objects."""

    def __init__(self) -> None:
        self._identities: dict[tuple[str, str], str] = {}

    def register(self, entity: str, identifier: str, source_key: str) -> None:
        key = (entity, identifier)
        if key in self._identities and self._identities[key] != source_key:
            raise CollisionError(
                f"Identity collision in {entity}: {identifier!r} "
                f"({self._identities[key]!r} versus {source_key!r})"
            )
        
        self._identities[key] = source_key
