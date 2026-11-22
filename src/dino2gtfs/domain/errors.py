"""Domain and application errors."""

class ConverterError(Exception):
    """Base error for expected converter failures."""

class ConfigurationError(ConverterError):
    """Invalid user configuration or profile."""

class InputError(ConverterError):
    """Invalid DINO input archive or records."""

class CollisionError(ConverterError):
    """Duplicate or conflicting canonical identity."""

class ConversionNotImplementedError(ConverterError):
    """A selected profile does not yet implement GTFS conversion."""
