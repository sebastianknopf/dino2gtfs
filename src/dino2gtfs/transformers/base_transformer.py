"""Shared functionality for GTFS object transformers."""
import hashlib
import math
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Collection, Iterable, Mapping
from typing import Any

from dino2gtfs.domain.errors import ConfigurationError, InputError
from dino2gtfs.domain.id_template import IdTemplate
from dino2gtfs.transformers.intf_transformer import Transformer


class TransformerBase(Transformer, ABC):
    """Provide shared template handling and canonical entity identity."""

    @abstractmethod
    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        """Transform imported data into a GTFS object table."""

    @abstractmethod
    def fingerprint_components(
        self, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> tuple[str, ...]:
        """Return ordered canonical identity components for the entity."""

    def configured_defaults(
        self, database: sqlite3.Connection, settings: Mapping[str, Any], entity: str
    ) -> dict[str, object]:
        """Validate all configured defaults against the GTFS schema."""
        defaults = settings.get("defaults", {})
        if not isinstance(defaults, Mapping):
            raise ConfigurationError("defaults must be a mapping")

        for name, values in defaults.items():
            if not isinstance(name, str) or not isinstance(values, Mapping):
                raise ConfigurationError(f"defaults.{name} must be an entity mapping")
            
            table = f"gtfs_{name}"
            exists = database.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            if not exists:
                raise ConfigurationError(f"Unknown GTFS entity in defaults: {name}")
            
            columns = {row[1] for row in database.execute("SELECT * FROM pragma_table_info(?)", (table,))}
            for field, value in values.items():
                if not isinstance(field, str) or field not in columns:
                    raise ConfigurationError(f"Unknown GTFS field in defaults.{name}: {field!r}")
                
                if field == "id" or field.endswith("_id") or field == "parent_station":
                    raise ConfigurationError(f"ID field defaults.{name}.{field} is not allowed; configure ids instead")
                
                if value is not None and not isinstance(value, (str, int, float)):
                    raise ConfigurationError(f"defaults.{name}.{field} must be a scalar SQLite value")
                
                if isinstance(value, int) and not -(2**63) <= value < 2**63:
                    raise ConfigurationError(f"defaults.{name}.{field} is outside the SQLite integer range")
                
                if isinstance(value, float) and not math.isfinite(value):
                    raise ConfigurationError(f"defaults.{name}.{field} must be finite")

        return dict(defaults.get(entity, {}))

    def apply_defaults(self, values: Mapping[str, object], defaults: Mapping[str, object]) -> dict[str, object]:
        """Override transformed values with the configured entity defaults."""
        return {**values, **defaults}

    def insert_with_defaults(
        self, database: sqlite3.Connection, entity: str, fields: tuple[str, ...],
        rows: Iterable[Mapping[str, object]], defaults: Mapping[str, object]
    ) -> None:
        """Insert transformed rows with every configured default column."""
        columns = tuple(dict.fromkeys((*fields, *defaults)))
        quoted_columns = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        table = '"gtfs_' + entity.replace('"', '""') + '"'
        merged_rows = (self.apply_defaults(row, defaults) for row in rows)
        
        database.executemany(
            f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders})",
            (tuple(row[column] for column in columns) for row in merged_rows),
        )

    def make_id_template(
        self, settings: Mapping[str, Any], entity: str, sources: Mapping[str, str]
    ) -> IdTemplate:
        if "gtfs" in sources:
            raise ValueError("gtfs is reserved for virtual template fields")
        
        return IdTemplate(settings, entity, {**sources, "gtfs": "GTFS context"})

    def validate_template_fields(self, template: IdTemplate, fields: Mapping[str, Collection[str]]) -> None:
        if "gtfs" in fields:
            raise ValueError("gtfs is reserved for virtual template fields")
        
        template.validate_fields({**fields, "gtfs": ("entity", "canonical", "fingerprint")})

    def render_id(
        self, template: IdTemplate, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> str:
        if "gtfs" in sources:
            raise ValueError("gtfs is reserved for virtual template fields")
        
        virtual = {"entity": template.entity}
        
        needs_key = template.requires_field("gtfs", "canonical")
        needs_fingerprint = template.requires_field("gtfs", "fingerprint")
        if needs_key or needs_fingerprint:
            components = self.fingerprint_components(sources, context)
            if not components or any(not isinstance(component, str) for component in components):
                field = "fingerprint" if needs_fingerprint else "canonical"
                
                raise InputError(f"{context}: {field} requires canonical string components")
            
            encoded = [component.encode("utf-8") for component in components]
            if needs_key:
                virtual["canonical"] = "".join(
                    f"{len(data)}:{component}" for component, data in zip(components, encoded)
                )
            
            if needs_fingerprint:
                digest = hashlib.sha256()
                for data in encoded:
                    digest.update(len(data).to_bytes(8, "big"))
                    digest.update(data)
                
                virtual["fingerprint"] = digest.hexdigest()
        
        return template.render({**sources, "gtfs": virtual}, context)
