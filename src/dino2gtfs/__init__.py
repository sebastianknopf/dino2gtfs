"""DINO to GTFS Schedule conversion package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dino2gtfs")
except PackageNotFoundError:
    __version__ = "0.0.0"
