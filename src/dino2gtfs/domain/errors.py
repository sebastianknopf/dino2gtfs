"""Domain and application errors."""

class ConverterError(Exception):
    """Base error for expected converter failures."""

class ConfigurationError(ConverterError):
    """Invalid user configuration or profile."""

class InputError(ConverterError):
    """Invalid DINO input archive or records."""

class CollisionError(ConverterError):
    """Duplicate or conflicting canonical identity."""

class ExportError(ConverterError):
    """GTFS data or output could not be exported."""
