"""Configurable GTFS identifier templates."""
from collections.abc import Collection, Mapping
from string import Formatter
from typing import Any

from dino2gtfs.domain.errors import ConfigurationError, InputError


class IdTemplate:
    """Validate and render template-mode IDs from named source fields.

    Sources map placeholder namespaces to descriptions for diagnostics. Field
    names are matched case-insensitively; values must be non-empty strings.
    """

    def __init__(self, settings: Mapping[str, Any], entity: str, sources: Mapping[str, str]) -> None:
        self._config_key = f"ids.{entity}"
        self._sources = dict(sources)
        ids = settings.get("ids")
        if not isinstance(ids, Mapping) or not isinstance(ids.get(entity), Mapping):
            raise ConfigurationError(f"{self._config_key} must be a mapping with mode and template")

        config = ids[entity]
        if config.get("mode") != "template":
            raise ConfigurationError(f"{self._config_key}.mode must be template")

        template = config.get("template")
        if not isinstance(template, str) or not template.strip():
            raise ConfigurationError(f"{self._config_key}.template must be a non-empty string")

        try:
            parts = list(Formatter().parse(template))
        except ValueError as exc:
            raise ConfigurationError(f"Invalid {self._config_key}.template: {exc}") from exc

        self._parts: list[tuple[str, str | None]] = []
        for literal, field, format_spec, conversion in parts:
            if field is not None:
                source, separator, name = field.partition(".")
                if source not in self._sources or not separator or not name or format_spec or conversion is not None:
                    raise ConfigurationError(f"Invalid placeholder in {self._config_key}.template: {field!r}")
            
            self._parts.append((literal, field))

    def validate_fields(self, fields: Mapping[str, Collection[str]]) -> None:
        """Check source field availability even when no records are present."""
        normalized = {source: {name.upper() for name in names} for source, names in fields.items()}
        for _, field in self._parts:
            if field is not None:
                source, _, name = field.partition(".")
                if name.upper() not in normalized.get(source, set()):
                    raise InputError(
                        f"{self._sources[source]} is missing column for {self._config_key}.template: {field}"
                    )

    def render(self, values: Mapping[str, Mapping[str, object]], context: str) -> str:
        """Render an ID, reporting missing values with their source context."""
        normalized = {source: {key.upper(): value for key, value in row.items()} for source, row in values.items()}
        
        result = []
        for literal, field in self._parts:
            result.append(literal)
            if field is not None:
                source, _, name = field.partition(".")
                value = normalized.get(source, {}).get(name.upper())
                
                if not isinstance(value, str) or not value.strip():
                    raise InputError(f"{context}: {field} is required by {self._config_key}.template")
                
                result.append(value)

        identifier = "".join(result)
        if not identifier.strip():
            raise InputError(f"{context}: {self._config_key}.template produced an empty ID")
        
        return identifier
