"""Transform DINO operators into GTFS agencies."""
import logging
import sqlite3
from collections.abc import Mapping
from typing import Any

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.transformers.base_transformer import TransformerBase

LOGGER = logging.getLogger(__name__)
NAME_COLUMNS = ("OP_TRADING_NAME", "OP_LONG_NAME", "OP_PUBLIC_SHORT")


class AgencyTransformer(TransformerBase):
    """Create one agency per DINO operator code."""

    def fingerprint_components(
        self, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> tuple[str, ...]:
        code = sources.get("operator", {}).get("OP_CODE")
        if not isinstance(code, str) or not code.strip():
            raise InputError(f"{context}: OP_CODE is required")
        return (code,)

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        defaults = self.configured_defaults(database, settings, "agency")

        for field in ("agency_url", "agency_timezone"):
            if not isinstance(defaults.get(field), str) or not defaults[field].strip():
                raise ConfigurationError(f"defaults.agency.{field} must be a non-empty string")
        
        language = defaults.get("agency_lang")
        if language is not None and (not isinstance(language, str) or not language.strip()):
            raise ConfigurationError("defaults.agency.agency_lang must be a non-empty string or null")
        
        if "agency_name" in defaults and (
            not isinstance(defaults["agency_name"], str) or not defaults["agency_name"].strip()
        ):
            raise ConfigurationError("defaults.agency.agency_name must be a non-empty string")

        template = self.make_id_template(settings, "agency", {"operator": "operator.din"})

        column_names = [row[1].upper() for row in database.execute('PRAGMA table_info("dino_operator")')]
        columns = set(column_names)
        if not columns:
            raise InputError("Missing DINO source file operator.din")
        
        if "OP_CODE" not in columns or ("agency_name" not in defaults and not columns.intersection(NAME_COLUMNS)):
            raise InputError("operator.din requires OP_CODE and at least one public name column")
        
        self.validate_template_fields(template, {"operator": columns})

        operators: dict[str, tuple[str, str, int]] = {}
        identifiers: dict[str, tuple[str, int]] = {}
        for rowid, *values in database.execute('SELECT rowid, * FROM "dino_operator"'):
            row = dict(zip(column_names, values))
            code = row["OP_CODE"]
            if not isinstance(code, str) or not code.strip():
                raise InputError(f"operator.din row {rowid}: OP_CODE is required")
            
            name = defaults.get("agency_name")
            if name is None:
                name = next((row[column] for column in NAME_COLUMNS if isinstance(row.get(column), str) and row[column].strip()), None)
            if name is None:
                raise InputError(f"operator.din row {rowid}: public operator name is required for {code!r}")

            identifier = self.render_id(template, {"operator": row}, f"operator.din row {rowid}")

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

        self.insert_with_defaults(
            database, "agency", ("agency_id", "agency_name"),
            ({"agency_id": identifier, "agency_name": name} for identifier, name, _ in operators.values()),
            defaults,
        )
        
        LOGGER.info("transformed %d DINO operators into GTFS agencies", len(operators))
