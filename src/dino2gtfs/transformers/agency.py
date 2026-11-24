"""Transform DINO operators into GTFS agencies."""
import logging
import sqlite3
from collections.abc import Mapping
from string import Formatter
from typing import Any

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.transformers.intf_transformer import Transformer

LOGGER = logging.getLogger(__name__)
NAME_COLUMNS = ("OP_TRADING_NAME", "OP_LONG_NAME", "OP_PUBLIC_SHORT")


class AgencyTransformer(Transformer):
    """Create one agency per DINO operator code."""

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        defaults = settings.get("defaults")
        if not isinstance(defaults, Mapping):
            raise ConfigurationError("defaults must be a mapping")
        
        agency = defaults.get("agency")
        if not isinstance(agency, Mapping):
            raise ConfigurationError("defaults.agency must be a mapping with url and timezone")

        for field in ("url", "timezone"):
            if not isinstance(agency.get(field), str) or not agency[field].strip():
                raise ConfigurationError(f"defaults.agency.{field} must be a non-empty string")
        
        language = agency.get("language")
        if language is not None and (not isinstance(language, str) or not language.strip()):
            raise ConfigurationError("defaults.agency.language must be a non-empty string or null")

        ids = settings.get("ids")
        if not isinstance(ids, Mapping) or not isinstance(ids.get("agency"), Mapping):
            raise ConfigurationError("ids.agency must be a mapping with mode and template")
        
        agency_ids = ids["agency"]
        if agency_ids.get("mode") != "template":
            raise ConfigurationError("ids.agency.mode must be template")
        
        template = agency_ids.get("template")
        if not isinstance(template, str) or not template.strip():
            raise ConfigurationError("ids.agency.template must be a non-empty string")
        
        try:
            parts = list(Formatter().parse(template))
        except ValueError as exc:
            raise ConfigurationError(f"Invalid ids.agency.template: {exc}") from exc
        for _, field, format_spec, conversion in parts:
            if field is not None and (
                not field.startswith("operator.") or not field[len("operator."):]
                or format_spec or conversion is not None
            ):
                raise ConfigurationError(f"Invalid placeholder in ids.agency.template: {field!r}")

        column_names = [row[1].upper() for row in database.execute('PRAGMA table_info("dino_operator")')]
        columns = set(column_names)
        if not columns:
            raise InputError("Missing DINO source file operator.din")
        
        if "OP_CODE" not in columns or not columns.intersection(NAME_COLUMNS):
            raise InputError("operator.din requires OP_CODE and at least one public name column")
        
        for _, field, _, _ in parts:
            if field is not None and field[len("operator."):].upper() not in columns:
                raise InputError(f"operator.din is missing column for ids.agency.template: {field}")

        operators: dict[str, tuple[str, str, int]] = {}
        identifiers: dict[str, tuple[str, int]] = {}
        for rowid, *values in database.execute('SELECT rowid, * FROM "dino_operator"'):
            row = dict(zip(column_names, values))
            code = row["OP_CODE"]
            if not isinstance(code, str) or not code.strip():
                raise InputError(f"operator.din row {rowid}: OP_CODE is required")
            
            name = next((row[column] for column in NAME_COLUMNS if isinstance(row.get(column), str) and row[column].strip()), None)
            if name is None:
                raise InputError(f"operator.din row {rowid}: public operator name is required for {code!r}")

            id_parts = []
            for literal, field, _, _ in parts:
                id_parts.append(literal)
                if field is not None:
                    value = row[field[len("operator."):].upper()]
                    if not isinstance(value, str) or not value.strip():
                        raise InputError(f"operator.din row {rowid}: {field} is required by ids.agency.template")
                    
                    id_parts.append(value)
            
            identifier = "".join(id_parts)
            if not identifier.strip():
                raise InputError(f"operator.din row {rowid}: ids.agency.template produced an empty ID")

            if code in operators and operators[code][:2] != (identifier, name):
                raise CollisionError(
                    f"Conflicting gtfs_agency identity for OP_CODE {code!r}: "
                    f"{operators[code][0]!r} and {identifier!r} "
                    f"(operator.din rows {operators[code][2]} and {rowid})"
                )
            
            if identifier in identifiers and identifiers[identifier][0] != code:
                raise CollisionError(
                    f"Identity collision in gtfs_agency: {identifier!r} "
                    f"(operator.din rows {identifiers[identifier][1]} and {rowid})"
                )
            
            operators.setdefault(code, (identifier, name, rowid))
            identifiers.setdefault(identifier, (code, rowid))
            
        if not operators:
            raise InputError("operator.din contains no operators")

        existing_ids = {row[0] for row in database.execute("SELECT agency_id FROM gtfs_agency")}
        for identifier, _, rowid in operators.values():
            if identifier in existing_ids:
                raise CollisionError(
                    f"Identity collision in gtfs_agency: {identifier!r} "
                    f"(existing agency and operator.din row {rowid})"
                )

        database.executemany(
            "INSERT INTO gtfs_agency (agency_id, agency_name, agency_url, agency_timezone, agency_lang) "
            "VALUES (?, ?, ?, ?, ?)",
            ((identifier, name, agency["url"], agency["timezone"], language)
             for identifier, name, _ in operators.values()),
        )
        
        LOGGER.info("transformed %d DINO operators into GTFS agencies", len(operators))
