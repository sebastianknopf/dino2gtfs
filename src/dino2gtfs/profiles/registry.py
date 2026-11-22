"""Explicit registry of supported profiles."""
from dino2gtfs.application.interfaces import Profile
from dino2gtfs.domain.errors import ConfigurationError
from dino2gtfs.profiles.dino212_default.profile import Dino212DefaultProfile

_PROFILES: dict[str, Profile] = {"dino212_default": Dino212DefaultProfile()}

def get_profile(name: str) -> Profile:
    try:
        return _PROFILES[name]
    except KeyError as exc:
        raise ConfigurationError(f"Unknown profile: {name}") from exc

def list_profiles() -> tuple[str, ...]:
    return tuple(sorted(_PROFILES))
