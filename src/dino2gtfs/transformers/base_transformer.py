"""Shared functionality for GTFS object transformers."""
import hashlib
import math
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Collection, Iterable, Iterator, Mapping
from typing import Any

from dino2gtfs.domain.errors import ConfigurationError, InputError
from dino2gtfs.domain.id_template import IdTemplate
from dino2gtfs.transformers.intf_transformer import Transformer


Row = dict[str, object]
SourceKey = tuple[str, ...]


def source_columns(
    database: sqlite3.Connection, table: str, required: tuple[str, ...], optional: bool = False
) -> set[str]:
    columns = {row[1].upper() for row in database.execute("SELECT * FROM pragma_table_info(?)", ("dino_" + table,))}
    if not columns:
        if optional:
            return set()
        
        raise InputError(f"Missing DINO source file {table}.din")
    
    missing = set(required) - columns
    if missing:
        raise InputError(f"{table}.din is missing required columns: {', '.join(sorted(missing))}")
    
    return columns


def source_rows(database: sqlite3.Connection, table: str) -> Iterator[tuple[int, Row]]:
    cursor = database.execute(f'SELECT rowid, * FROM "dino_{table}"')
    names = [column[0].upper() for column in cursor.description[1:]]
    for rowid, *values in cursor:
        yield rowid, dict(zip(names, values))


def required_source_value(row: Mapping[str, object], name: str, context: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{context}: {name} is required")
    
    return value


def optional_source_value(row: Mapping[str, object], name: str) -> str | None:
    value = row.get(name)
    return value if isinstance(value, str) and value.strip() else None


def source_key(row: Mapping[str, object], names: tuple[str, ...], context: str) -> SourceKey:
    return tuple(required_source_value(row, name, context) for name in names)


class TransformerBase(Transformer, ABC):
    """Provide shared template handling and canonical entity identity."""

    @abstractmethod
    def initialize(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        """Initialize transformer-specific tables before transformation."""

    @abstractmethod
    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        """Transform imported data into a GTFS object table."""

    @abstractmethod
    def fingerprint_components(
        self, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> tuple[str, ...]:
        """Return canonical components using source rows and computed gtfs fields."""

    def configured_fields(
        self, database: sqlite3.Connection, settings: Mapping[str, Any], entity: str, section: str
    ) -> dict[str, object]:
        """Validate configured defaults or overrides against the GTFS schema."""
        configured = settings.get(section, {})
        if not isinstance(configured, Mapping):
            raise ConfigurationError(f"{section} must be a mapping")

        for name, values in configured.items():
            if not isinstance(name, str) or not isinstance(values, Mapping):
                raise ConfigurationError(f"{section}.{name} must be an entity mapping")
            
            table = f"gtfs_{name}"
            exists = database.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            if not exists:
                raise ConfigurationError(f"Unknown GTFS entity in {section}: {name}")
            
            columns = {row[1] for row in database.execute("SELECT * FROM pragma_table_info(?)", (table,))}
            for field, value in values.items():
                if not isinstance(field, str) or field not in columns:
                    raise ConfigurationError(f"Unknown GTFS field in {section}.{name}: {field!r}")
                
                if field == "id" or field.endswith("_id") or field == "parent_station":
                    raise ConfigurationError(f"ID field {section}.{name}.{field} is not allowed; configure ids instead")
                
                if value is not None and not isinstance(value, (str, int, float)):
                    raise ConfigurationError(f"{section}.{name}.{field} must be a scalar SQLite value")
                
                if isinstance(value, int) and not -(2**63) <= value < 2**63:
                    raise ConfigurationError(f"{section}.{name}.{field} is outside the SQLite integer range")
                
                if isinstance(value, float) and not math.isfinite(value):
                    raise ConfigurationError(f"{section}.{name}.{field} must be finite")

        return dict(configured.get(entity, {}))

    def apply_configuration(
        self, values: Mapping[str, object], defaults: Mapping[str, object], overrides: Mapping[str, object]
    ) -> dict[str, object]:
        """Fill absent or blank values from defaults, then apply explicit overrides."""
        result = dict(values)
        for field, default in defaults.items():
            value = result.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                result[field] = default
        
        return {**result, **overrides}

    def insert_with_configuration(
        self, database: sqlite3.Connection, entity: str, fields: tuple[str, ...],
        rows: Iterable[Mapping[str, object]], defaults: Mapping[str, object], overrides: Mapping[str, object]
    ) -> None:
        """Insert transformed rows with every configured default and override column."""
        columns = tuple(dict.fromkeys((*fields, *defaults, *overrides)))
        quoted_columns = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        table = '"gtfs_' + entity.replace('"', '""') + '"'
        merged_rows = (self.apply_configuration(row, defaults, overrides) for row in rows)
        
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

    def validate_template_fields(
        self, template: IdTemplate, fields: Mapping[str, Collection[str]], virtual_fields: Collection[str] = ()
    ) -> None:
        if "gtfs" in fields:
            raise ValueError("gtfs is reserved for virtual template fields")
        
        template.validate_fields({**fields, "gtfs": ("entity", "canonical", "fingerprint", *virtual_fields)})

    def render_id(
        self, template: IdTemplate, sources: Mapping[str, Mapping[str, object]], context: str,
        virtual_fields: Mapping[str, str] | None = None,
        optional_fields: Collection[tuple[str, str]] = (),
    ) -> str:
        if "gtfs" in sources:
            raise ValueError("gtfs is reserved for virtual template fields")
        
        virtual = {"entity": template.entity}
        if virtual_fields:
            if {key.upper() for key in virtual_fields} & {"ENTITY", "CANONICAL", "FINGERPRINT"}:
                raise ValueError("shared GTFS template fields cannot be overridden")
            virtual.update(virtual_fields)
        
        needs_key = template.requires_field("gtfs", "canonical")
        needs_fingerprint = template.requires_field("gtfs", "fingerprint")
        if needs_key or needs_fingerprint:
            components = self.fingerprint_components({**sources, "gtfs": virtual}, context)
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
        
        return template.render({**sources, "gtfs": virtual}, context, optional_fields=optional_fields)
