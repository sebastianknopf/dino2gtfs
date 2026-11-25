"""Transform DINO lines into GTFS routes."""
import logging
import sqlite3
from collections.abc import Mapping
from typing import Any

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.transformers.base_transformer import (
    Row,
    SourceKey,
    TransformerBase,
    optional_source_value as _optional,
    required_source_value as _required,
    source_columns as _columns,
    source_key as _source_key,
    source_rows as _rows,
)

LOGGER = logging.getLogger(__name__)
LINE_KEY = ("VERSION", "LINE_NR", "STR_LINE_VAR", "LINE_DIR_NR")


def _route_type(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(f"{context} must be a non-negative GTFS route_type integer")
    
    if value >= 2**63:
        raise ConfigurationError(f"{context} is outside the SQLite integer range")
    
    return value


def _mode_mapping(settings: Mapping[str, Any]) -> dict[str, int]:
    mapping = settings.get("mapping", {})
    if not isinstance(mapping, Mapping):
        raise ConfigurationError("mapping must be a mapping")
    
    modes = mapping.get("mode_of_transport", {})
    if not isinstance(modes, Mapping):
        raise ConfigurationError("mapping.mode_of_transport must be a mapping")

    result: dict[str, int] = {}
    for key, value in modes.items():
        if isinstance(key, bool) or not isinstance(key, (str, int)) or not str(key).strip():
            raise ConfigurationError("mapping.mode_of_transport keys must be non-empty MOT_NR strings or integers")
        
        key = str(key)
        
        route_type = _route_type(value, f"mapping.mode_of_transport[{key!r}]")
        if key in result:
            raise ConfigurationError(f"Duplicate MOT_NR in mapping.mode_of_transport: {key!r}")
        
        result[key] = route_type
    
    return result


class RoutesTransformer(TransformerBase):
    """Consolidate line variants by line number, operator and mapped mode."""

    def initialize(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        database.execute(
            "CREATE TABLE map_line_2_routes "
            "(VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, gtfs_id TEXT)"
        )

    def fingerprint_components(
        self, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> tuple[str, ...]:
        return (
            _required(sources["line"], "LINE_NR", context),
            _optional(sources["line"], "OP_CODE") or "",
            _required(sources["gtfs"], "route_type", context),
        )

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        defaults = self.configured_defaults(database, settings, "routes")
        default_type = (
            _route_type(defaults.pop("route_type"), "defaults.routes.route_type")
            if "route_type" in defaults else None
        )
        
        modes = _mode_mapping(settings)

        for field in ("route_short_name", "route_long_name"):
            if field in defaults and defaults[field] is not None and (
                not isinstance(defaults[field], str) or not defaults[field].strip()
            ):
                raise ConfigurationError(f"defaults.routes.{field} must be a non-empty string or null")

        ids = settings.get("ids")
        if not isinstance(ids, Mapping) or not isinstance(ids.get("routes"), Mapping):
            raise ConfigurationError("ids.routes must be a mapping with mode")
        
        mode = ids["routes"].get("mode")
        if mode not in ("global", "template"):
            raise ConfigurationError("ids.routes.mode must be global or template")
        
        template = self.make_id_template(settings, "routes", {"line": "line.din"}) if mode == "template" else None

        columns = _columns(database, "line", LINE_KEY)
        if template is not None:
            self.validate_template_fields(template, {"line": columns | {"OP_CODE"}}, ("route_type",))
        
        if not database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'map_operator_2_agency'"
        ).fetchone():
            raise InputError("Routes require map_operator_2_agency; run AgencyTransformer first")

        agencies = {row[0] for row in database.execute("SELECT agency_id FROM gtfs_agency")}
        agency_mappings: dict[tuple[object, object], set[object]] = {}
        for version, code, identifier in database.execute("SELECT VERSION, OP_CODE, gtfs_id FROM map_operator_2_agency"):
            agency_mappings.setdefault((version, code), set()).add(identifier)

        existing_ids = {row[0] for row in database.execute("SELECT route_id FROM gtfs_routes")}
        routes: dict[SourceKey, tuple[str, Row, str]] = {}
        identifiers: dict[str, str] = {}
        mappings: dict[SourceKey, tuple[SourceKey, str]] = {}

        for rowid, row in _rows(database, "line"):
            context = f"line.din row {rowid}"
            key = _source_key(row, LINE_KEY, context)
            code = _optional(row, "OP_CODE") or ""
            agency_id = None
            if not code:
                LOGGER.warning("%s: OP_CODE is missing; creating route without an agency assignment", context)
            else:
                agency_ids = agency_mappings.get((key[0], code), set())
                if not agency_ids:
                    raise InputError(f"{context}: no agency mapping for VERSION {key[0]!r}, OP_CODE {code!r}")
                
                if len(agency_ids) != 1:
                    raise CollisionError(f"{context}: conflicting map_operator_2_agency entries for OP_CODE {code!r}")
                
                agency_id = next(iter(agency_ids))
                if not isinstance(agency_id, str) or not agency_id.strip() or agency_id not in agencies:
                    raise InputError(f"{context}: agency mapping references missing gtfs_agency ID {agency_id!r}")

            mot = _optional(row, "MOT_NR")
            route_type = modes.get(mot) if mot is not None else None
            if route_type is None:
                route_type = default_type
            
            if route_type is None:
                raise InputError(
                    f"{context}: no mapping.mode_of_transport entry for MOT_NR {mot!r} "
                    "and no defaults.routes.route_type"
                )
            
            virtual = {"route_type": str(route_type)}
            identity = self.fingerprint_components({"line": row, "gtfs": virtual}, context)
            if mode == "global":
                global_id = _required(row, "GLOBAL_ID", context)
                identifier = f"route:{global_id}:{code}:{route_type}"
            else:
                assert template is not None
                identifier = self.render_id(
                    template, {"line": row}, context, virtual, optional_fields=(("line", "OP_CODE"),)
                )

            values = self.apply_defaults({
                "route_id": identifier, "agency_id": agency_id,
                "route_short_name": _optional(row, "LINE_NAME"), "route_long_name": None,
                "route_type": route_type,
            }, defaults)
            
            if key in mappings and mappings[key][0] != identity:
                raise CollisionError(
                    f"conflicting gtfs_routes source identity: {identifier!r} ({mappings[key][1]} and {context})"
                )

            routes[identity] = (identifier, values, context)
            mappings.setdefault(key, (identity, context))

        for identifier, values, context in routes.values():
            if identifier in existing_ids:
                raise CollisionError(f"identity collision in gtfs_routes: {identifier!r} (existing route and {context})")

            if identifier in identifiers:
                raise CollisionError(
                    f"identity collision in gtfs_routes: {identifier!r} ({identifiers[identifier]} and {context})"
                )
            identifiers[identifier] = context

            if not values["route_short_name"] and not values["route_long_name"]:
                raise InputError(f"LINE_NAME missing in {context}")

        self.insert_with_defaults(
            database, "routes", ("route_id", "agency_id", "route_short_name", "route_long_name", "route_type"),
            (values for _, values, _ in routes.values()), defaults,
        )
        
        database.executemany(
            "INSERT INTO map_line_2_routes (VERSION, LINE_NR, STR_LINE_VAR, LINE_DIR_NR, gtfs_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ((*key, routes[identity][0]) for key, (identity, _) in mappings.items()),
        )
        
        LOGGER.info("transformed %d DINO lines into GTFS routes", len(routes))
