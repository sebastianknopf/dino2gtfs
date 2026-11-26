"""Transform DINO lines into GTFS routes."""
import logging
import sqlite3
from collections.abc import Mapping
from typing import Any

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.domain.id_template import IdTemplate
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

# GTFS fields written by this transformer
GTFS_FIELDS = ("route_id", "agency_id", "route_short_name", "route_long_name", "route_type")
# DINO columns identifying a line (source mapping key)
LINE_KEY = ("VERSION", "LINE_NR", "STR_LINE_VAR", "LINE_DIR_NR")
# DINO columns identifying a route across versions (canonical ID components)
ROUTE_KEY = ("VERSION", "BRANCH_NR", "LINE_NR")

# A merged route: ID, GTFS values, row context, global ID and LINE_NAME.
Route = tuple[str, Row, str, str | None, str | None]


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
            raise ConfigurationError("mapping.mode_of_transport keys must be non-empty TMOT_NR strings or integers")
        
        key = str(key)
        
        route_type = _route_type(value, f"mapping.mode_of_transport[{key!r}]")
        if key in result:
            raise ConfigurationError(f"Duplicate TMOT_NR in mapping.mode_of_transport: {key!r}")
        
        result[key] = route_type
    
    return result


class RoutesTransformer(TransformerBase):
    """Consolidate lines by global ID or by branch and line number."""

    def initialize(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        database.execute(
            "CREATE TABLE map_line_2_routes "
            "(VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, gtfs_id TEXT)"
        )

    def fingerprint_components(
        self, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> tuple[str, ...]:
        name = _optional(sources["line"], "LINE_NAME")
        if name is None:
            name = _required(sources["gtfs"], "route_short_name", context)

        return (
            name,
            _required(sources["gtfs"], "route_type", context),
        )

    def canonical_value(
        self, sources: Mapping[str, Mapping[str, object]], context: str,
        components: tuple[str, ...] | None = None,
    ) -> str:
        return ":".join(_source_key(sources["line"], ROUTE_KEY, context))

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        # Step 1: read and validate the configuration
        self._configure(database, settings)
        modes = _mode_mapping(settings)

        # Step 2: validate the DINO source tables
        columns = _columns(
            database, "line", (*LINE_KEY, "BRANCH_NR") if self._template is not None else LINE_KEY
        )
        if self._template is not None:
            self.validate_template_fields(self._template, {"line": columns | {"OP_CODE"}}, ("route_type",))

        # Step 3: load the lookups for route types and agencies
        transport_modes = self._read_transport_modes(database)
        self._agencies = self._read_agency_mappings(database)

        # Step 4: merge lines into routes
        routes, mappings = self._convert_lines(database, modes, transport_modes)

        # Step 5: check the merged routes
        existing_ids = {row[0] for row in database.execute("SELECT route_id FROM gtfs_routes")}
        self._validate_routes(routes, existing_ids)

        # Step 6: write routes and the line mapping table
        self.insert_with_configuration(
            database, "routes", GTFS_FIELDS,
            (values for _, values, _, _, _ in routes.values()), self._defaults, self._overrides,
        )
        database.executemany(
            "INSERT INTO map_line_2_routes (VERSION, LINE_NR, STR_LINE_VAR, LINE_DIR_NR, gtfs_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ((*key, routes[identity][0]) for key, (identity, _) in mappings.items()),
        )

        LOGGER.info("transformed %d DINO lines into GTFS routes", len(routes))

    # -- configuration -------------------------------------------------------------------------

    def _configure(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        """Load defaults, overrides, ID mode and ID template."""
        self._defaults = self.configured_fields(database, settings, "routes", "defaults")
        self._overrides = self.configured_fields(database, settings, "routes", "overrides")
        for section, configured in (("defaults", self._defaults), ("overrides", self._overrides)):
            if "route_type" in configured:
                _route_type(configured["route_type"], f"{section}.routes.route_type")

            for field in ("route_short_name", "route_long_name"):
                if field in configured and configured[field] is not None and (
                    not isinstance(configured[field], str) or not configured[field].strip()
                ):
                    raise ConfigurationError(f"{section}.routes.{field} must be a non-empty string or null")

        ids = settings.get("ids")
        if not isinstance(ids, Mapping) or not isinstance(ids.get("routes"), Mapping):
            raise ConfigurationError("ids.routes must be a mapping with mode")

        self._mode = ids["routes"].get("mode")
        if self._mode not in ("global", "template"):
            raise ConfigurationError("ids.routes.mode must be global or template")

        self._template: IdTemplate | None = None
        if self._mode == "template":
            self._template = self.make_id_template(settings, "routes", {"line": "line.din"})

    # -- lookups -------------------------------------------------------------------------------

    @staticmethod
    def _read_transport_modes(database: sqlite3.Connection) -> dict[SourceKey, tuple[str | None, str]]:
        """Resolve (VERSION, MOT_NR) to TMOT_NR using means_of_transport_desc."""
        result: dict[SourceKey, tuple[str | None, str]] = {}
        if not _columns(database, "means_of_transport_desc", ("VERSION", "MOT_NR", "TMOT_NR"), optional=True):
            return result

        for rowid, row in _rows(database, "means_of_transport_desc"):
            context = f"means_of_transport_desc.din row {rowid}"
            key = _source_key(row, ("VERSION", "MOT_NR"), context)
            tmot = _optional(row, "TMOT_NR")
            previous = result.get(key)
            if previous is not None and previous[0] != tmot:
                raise CollisionError(
                    f"conflicting TMOT_NR for VERSION {key[0]!r}, MOT_NR {key[1]!r} "
                    f"({previous[1]} and {context})"
                )

            result.setdefault(key, (tmot, context))

        return result

    @staticmethod
    def _read_agency_mappings(database: sqlite3.Connection) -> dict[tuple[object, object], set[object]]:
        """Read the operator to agency mapping created by the agency transformer."""
        if not database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'map_operator_2_agency'"
        ).fetchone():
            raise InputError("Routes require map_operator_2_agency; run AgencyTransformer first")

        mappings: dict[tuple[object, object], set[object]] = {}
        for version, code, identifier in database.execute(
            "SELECT VERSION, OP_CODE, gtfs_id FROM map_operator_2_agency"
        ):
            mappings.setdefault((version, code), set()).add(identifier)

        return mappings

    def _agency_for(self, row: Row, version: str, context: str, known: set[str]) -> str | None:
        """Look up the agency of a line; a missing OP_CODE yields no agency."""
        code = _optional(row, "OP_CODE") or ""
        if not code:
            LOGGER.warning("%s: OP_CODE is missing; creating route without an agency assignment", context)
            return None

        agency_ids = self._agencies.get((version, code), set())
        if not agency_ids:
            raise InputError(f"{context}: no agency mapping for VERSION {version!r}, OP_CODE {code!r}")

        if len(agency_ids) != 1:
            raise CollisionError(f"{context}: conflicting map_operator_2_agency entries for OP_CODE {code!r}")

        agency_id = next(iter(agency_ids))
        if not isinstance(agency_id, str) or not agency_id.strip() or agency_id not in known:
            raise InputError(f"{context}: agency mapping references missing gtfs_agency ID {agency_id!r}")

        return agency_id

    def _route_type_for(
        self, row: Row, version: str, context: str,
        modes: dict[str, int], transport_modes: dict[SourceKey, tuple[str | None, str]],
    ) -> int:
        """Resolve MOT_NR to TMOT_NR and map it to a GTFS route_type."""
        mot = _optional(row, "MOT_NR")
        description = transport_modes.get((version, mot)) if mot is not None else None
        tmot = description[0] if description is not None else None
        mapped_type = modes.get(tmot) if tmot is not None else None
        route_type = self.apply_configuration(
            {"route_type": mapped_type}, self._defaults, self._overrides
        ).get("route_type")
        if route_type is None:
            raise InputError(
                f"{context}: no mapping.mode_of_transport entry for TMOT_NR {tmot!r} "
                f"(MOT_NR {mot!r} via means_of_transport_desc.din) "
                "and no defaults.routes.route_type or overrides.routes.route_type"
            )

        return route_type

    # -- merging -------------------------------------------------------------------------------

    def _convert_lines(
        self, database: sqlite3.Connection, modes: dict[str, int],
        transport_modes: dict[SourceKey, tuple[str | None, str]],
    ) -> tuple[dict[SourceKey, Route], dict[SourceKey, tuple[SourceKey, str]]]:
        """Merge lines by global ID (global mode) or BRANCH_NR and LINE_NR (template mode)."""
        known_agencies = {row[0] for row in database.execute("SELECT agency_id FROM gtfs_agency")}
        routes: dict[SourceKey, Route] = {}
        mappings: dict[SourceKey, tuple[SourceKey, str]] = {}
        global_ids: dict[str, tuple[SourceKey, str]] = {}

        for rowid, row in _rows(database, "line"):
            context = f"line.din row {rowid}"
            key = _source_key(row, LINE_KEY, context)

            # Resolve agency and route type
            agency_id = self._agency_for(row, key[0], context, known_agencies)
            route_type = self._route_type_for(row, key[0], context, modes, transport_modes)

            # Resolve the route name, falling back to the configured default
            virtual = {"route_type": str(route_type)}
            name = _optional(row, "LINE_NAME")
            if name is None:
                default_name = self._defaults.get("route_short_name")
                if not isinstance(default_name, str) or not default_name.strip():
                    raise InputError(
                        f"{context}: LINE_NAME is required; configure defaults.routes.route_short_name"
                    )
                virtual["route_short_name"] = default_name

            # Determine merge identity and GTFS route ID
            global_id = _optional(row, "GLOBAL_ID")
            if self._mode == "global":
                global_id = _required(row, "GLOBAL_ID", context)
                identity: SourceKey = (global_id,)
                identifier = global_id
            else:
                identity = _source_key(row, ("BRANCH_NR", "LINE_NR"), context)
                if global_id is None:
                    LOGGER.warning("%s: GLOBAL_ID is missing", context)
                else:
                    previous_global = global_ids.get(global_id)
                    if previous_global is not None and previous_global[0] != identity:
                        LOGGER.warning(
                            "global ID collision in gtfs_routes: %r (%s and %s)",
                            global_id, previous_global[1], context,
                        )
                    global_ids.setdefault(global_id, (identity, context))

                assert self._template is not None
                identifier = self.render_id(
                    self._template, {"line": row}, context, virtual, optional_fields=(("line", "OP_CODE"),)
                )

            values = self.apply_configuration({
                "route_id": identifier, "agency_id": agency_id,
                "route_short_name": name, "route_long_name": None,
                "route_type": route_type,
            }, self._defaults, self._overrides)

            # Register the line; the last line of a merged route wins
            if key in mappings and mappings[key][0] != identity:
                raise CollisionError(
                    f"conflicting gtfs_routes source identity: {identifier!r} ({mappings[key][1]} and {context})"
                )

            previous = routes.get(identity)
            if self._mode == "global" and previous is not None and previous[4] != name:
                LOGGER.warning(
                    "%s: LINE_NAME %r differs from %r in %s for GLOBAL_ID %r; using the last line.din entry",
                    context, name, previous[4], previous[2], global_id,
                )

            routes[identity] = (identifier, values, context, global_id, name)
            mappings.setdefault(key, (identity, context))

        return routes, mappings

    @staticmethod
    def _validate_routes(routes: dict[SourceKey, Route], existing_ids: set[Any]) -> None:
        """Reject ID collisions and routes without any name."""
        identifiers: dict[str, str] = {}
        for identifier, values, context, _, _ in routes.values():
            if identifier in existing_ids:
                raise CollisionError(
                    f"identity collision in gtfs_routes: {identifier!r} (existing route and {context})"
                )

            if identifier in identifiers:
                raise CollisionError(
                    f"identity collision in gtfs_routes: {identifier!r} ({identifiers[identifier]} and {context})"
                )
            identifiers[identifier] = context

            if not values["route_short_name"] and not values["route_long_name"]:
                raise InputError(f"LINE_NAME missing in {context}")
