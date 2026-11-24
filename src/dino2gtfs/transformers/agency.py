"""Transform DINO operators into GTFS agencies."""
import logging
import sqlite3
from collections.abc import Mapping
from typing import Any

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.transformers.intf_transformer import Transformer

LOGGER = logging.getLogger(__name__)
NAME_COLUMNS = ("OP_TRADING_NAME", "OP_LONG_NAME", "OP_PUBLIC_SHORT")


class AgencyTransformer(Transformer):
    """Create one agency per DINO operator code."""

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        agency = settings.get("agency")
        if not isinstance(agency, Mapping):
            raise ConfigurationError("agency must be a mapping with url and timezone")

        for field in ("url", "timezone"):
            if not isinstance(agency.get(field), str) or not agency[field].strip():
                raise ConfigurationError(f"agency.{field} must be a non-empty string")
        
        language = agency.get("language")
        if language is not None and (not isinstance(language, str) or not language.strip()):
            raise ConfigurationError("agency.language must be a non-empty string or null")

        columns = {row[1].upper() for row in database.execute('PRAGMA table_info("dino_operator")')}
        if not columns:
            raise InputError("Missing DINO source file operator.din")
        if "OP_CODE" not in columns or not columns.intersection(NAME_COLUMNS):
            raise InputError("operator.din requires OP_CODE and at least one public name column")

        names = ", ".join(f'"{name}"' if name in columns else "NULL" for name in NAME_COLUMNS)
        operators: dict[str, tuple[str, int]] = {}
        for rowid, code, *name_values in database.execute(f'SELECT rowid, "OP_CODE", {names} FROM "dino_operator"'):
            if not isinstance(code, str) or not code.strip():
                raise InputError(f"operator.din row {rowid}: OP_CODE is required")
            
            name = next((value for value in name_values if isinstance(value, str) and value.strip()), None)
            if name is None:
                raise InputError(f"operator.din row {rowid}: public operator name is required for {code!r}")

            if code in operators and operators[code][0] != name:
                raise CollisionError(
                    f"Identity collision in gtfs_agency: {'agency:' + code!r} "
                    f"(operator.din rows {operators[code][1]} and {rowid})"
                )
            
            operators.setdefault(code, (name, rowid))

        if not operators:
            raise InputError("operator.din contains no operators")

        existing_ids = {row[0] for row in database.execute("SELECT agency_id FROM gtfs_agency")}
        for code, (_, rowid) in operators.items():
            if f"agency:{code}" in existing_ids:
                raise CollisionError(
                    f"Identity collision in gtfs_agency: {'agency:' + code!r} "
                    f"(existing agency and operator.din row {rowid})"
                )

        database.executemany(
            "INSERT INTO gtfs_agency (agency_id, agency_name, agency_url, agency_timezone, agency_lang) "
            "VALUES (?, ?, ?, ?, ?)",
            ((f"agency:{code}", name, agency["url"], agency["timezone"], language)
             for code, (name, _) in operators.items()),
        )
        
        LOGGER.info("transformed %d DINO operators into GTFS agencies", len(operators))
