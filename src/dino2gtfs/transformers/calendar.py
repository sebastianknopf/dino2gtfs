"""Transform DINO day attributes into GTFS calendar services."""
import logging
import sqlite3
from collections.abc import Mapping, Set as AbstractSet
from datetime import date, datetime
from typing import Any

from dino2gtfs.domain.calendar import decode_month_mask
from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.transformers.base_transformer import (
    Row,
    TransformerBase,
    optional_source_value as _optional,
    required_source_value as _required,
    source_columns as _columns,
    source_rows as _rows,
)

LOGGER = logging.getLogger(__name__)

# GTFS fields written by this transformer
CALENDAR_FIELDS = (
    "service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "start_date", "end_date",
)
CALENDAR_DATE_FIELDS = ("service_id", "date", "exception_type")
# DINO columns identifying a day attribute (canonical ID components)
DAY_ATTRIBUTE_KEY = ("VERSION", "DAY_ATTRIBUTE_NR")

# A service: version, day attribute number and optional restriction.
ServiceKey = tuple[str, str, str | None]


class CalendarTransformer(TransformerBase):
    """Create one service per version, day attribute and (optionally) trip restriction."""

    def initialize(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        database.execute(
            "CREATE TABLE map_calendar_2_calendar "
            "(VERSION TEXT, DAY_ATTRIBUTE_NR TEXT, RESTRICTION TEXT, gtfs_id TEXT)"
        )

    def fingerprint_components(
        self, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> tuple[str, ...]:
        attribute = sources["day_attribute"]
        components = [_required(attribute, field, context) for field in DAY_ATTRIBUTE_KEY]
        for extension in (_optional(attribute, "STR_DAY_ATTRIBUTE"), sources["gtfs"].get("restriction")):
            if isinstance(extension, str) and extension.strip():
                components.append(extension)

        return tuple(components)

    def canonical_value(
        self, sources: Mapping[str, Mapping[str, object]], context: str,
        components: tuple[str, ...] | None = None,
    ) -> str:
        return ":".join(components or self.fingerprint_components(sources, context))

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        # Step 1: read and validate the configuration
        calendar_defaults = self.configured_fields(database, settings, "calendar", "defaults")
        calendar_overrides = self.configured_fields(database, settings, "calendar", "overrides")
        date_defaults = self.configured_fields(database, settings, "calendar_dates", "defaults")
        date_overrides = self.configured_fields(database, settings, "calendar_dates", "overrides")

        ids = settings.get("ids")
        if not isinstance(ids, Mapping) or not isinstance(ids.get("calendar"), Mapping):
            raise ConfigurationError("ids.calendar must be a mapping with mode")

        if ids["calendar"].get("mode") != "template":
            raise ConfigurationError("ids.calendar.mode must be template")

        template = self.make_id_template(settings, "calendar", {"day_attribute": "day_attribute.din"})

        # Step 2: validate the DINO source tables
        attribute_columns = _columns(database, "day_attribute", DAY_ATTRIBUTE_KEY)
        _columns(database, "version", ("VERSION",))
        _columns(database, "day_type_calendar", ("VERSION", "DAY", "DAY_TYPE_NR"))
        _columns(database, "day_type_2_day_attribute", ("VERSION", "DAY_TYPE_NR", "DAY_ATTRIBUTE_NR"))
        _columns(database, "line", ("VERSION", "LINE_NR"))
        self.validate_template_fields(template, {"day_attribute": attribute_columns}, ("restriction",))

        # Step 3: read versions, their periods and the day type calendar
        self._read_day_types(database)
        self._read_versions(database)

        # Step 4: read lines, trip combinations and restrictions
        self._read_lines(database)
        trip_services = self._read_trip_services(database)
        self._read_restrictions(database)

        # Step 5: build the service keys with their line scope and render the service IDs
        services: dict[ServiceKey, tuple[str, str, set[str]]] = {}
        identifiers: dict[str, str] = {}
        existing_ids = {row[0] for row in database.execute("SELECT service_id FROM gtfs_calendar")}
        attributes: dict[tuple[str, str], tuple[int, Row]] = {}
        for rowid, row in _rows(database, "day_attribute"):
            context = f"day_attribute.din row {rowid}"
            attributes[(_required(row, "VERSION", context), _required(row, "DAY_ATTRIBUTE_NR", context))] = (rowid, row)

        for key in trip_services:
            if (key[0], key[1]) not in attributes:
                raise InputError(
                    f"trip.din references DAY_ATTRIBUTE_NR {key[1]!r} of VERSION {key[0]!r} "
                    "which is missing from day_attribute.din"
                )

        # Unrestricted services always exist; without trips they cover all lines of the version
        scopes: dict[ServiceKey, set[str]] = {
            (version, attribute, None): self._lines_by_version.get(version, set())
            for version, attribute in attributes
        }
        scopes.update(trip_services)

        for key, lines in scopes.items():
            rowid, row = attributes[(key[0], key[1])]
            context = f"day_attribute.din row {rowid}" + (f" with RESTRICTION {key[2]!r}" if key[2] else "")
            identifier = self.render_id(template, {"day_attribute": row}, context, {"restriction": key[2] or ""})
            if identifier in existing_ids:
                raise CollisionError(
                    f"Identity collision in gtfs_calendar: {identifier!r} (existing service and {context})"
                )

            if identifier in identifiers:
                raise CollisionError(
                    f"Identity collision in gtfs_calendar: {identifier!r} ({identifiers[identifier]} and {context})"
                )

            identifiers[identifier] = context
            services[key] = (identifier, context, lines)

        # Step 6: resolve the active dates of each service, preferring versions with higher priority
        # Step 7: merge services with exactly the same dates into the first service of that date pattern
        calendar_rows: list[dict[str, object]] = []
        exception_rows: list[dict[str, object]] = []
        merged_ids: dict[ServiceKey, str] = {}
        by_dates: dict[frozenset[int], str] = {}
        merged_count = 0
        for key, (identifier, context, lines) in services.items():
            dates = frozenset(self._service_dates(key, lines, context))
            if not dates:
                LOGGER.warning("%s: service %r has no active dates; no calendar rows written", context, identifier)
                merged_ids[key] = identifier
                continue

            if dates in by_dates:
                merged_ids[key] = by_dates[dates]
                merged_count += 1
                LOGGER.debug("%s: service %r has the same dates as %r; merged", context, identifier, by_dates[dates])
                continue

            by_dates[dates] = merged_ids[key] = identifier

            # Step 8: consolidate into calendar.txt with the minimal calendar_dates.txt exceptions
            calendar_row, exceptions = self._consolidate(identifier, dates)
            if calendar_row is not None:
                calendar_rows.append(calendar_row)
            exception_rows.extend(exceptions)

        # Step 9: write services and the calendar mapping table, keeping every source combination mapped
        self.insert_with_configuration(
            database, "calendar", CALENDAR_FIELDS, calendar_rows, calendar_defaults, calendar_overrides
        )
        self.insert_with_configuration(
            database, "calendar_dates", CALENDAR_DATE_FIELDS, exception_rows, date_defaults, date_overrides
        )
        database.executemany(
            "INSERT INTO map_calendar_2_calendar (VERSION, DAY_ATTRIBUTE_NR, RESTRICTION, gtfs_id) "
            "VALUES (?, ?, ?, ?)",
            ((*key, identifier) for key, identifier in merged_ids.items()),
        )

        LOGGER.info(
            "transformed %d DINO day attributes into %d GTFS services; merged %d services with identical dates "
            "(%d services remain, %d calendar rows, %d calendar date rows)",
            len(attributes), len(services), merged_count, len(services) - merged_count,
            len(calendar_rows), len(exception_rows),
        )

    # -- source reading ------------------------------------------------------------------------

    @staticmethod
    def _parse_date(value: str, field: str, context: str) -> int:
        """Parse a DINO YYYYMMDD date into a proleptic Gregorian ordinal."""
        try:
            return datetime.strptime(value.strip(), "%Y%m%d").date().toordinal()
        except ValueError as exc:
            raise InputError(f"{context}: invalid {field}: {value!r}") from exc

    def _read_day_types(self, database: sqlite3.Connection) -> None:
        """Read day types per date and the day attributes each day type belongs to."""
        self._day_types: dict[str, dict[int, str]] = {}
        for rowid, row in _rows(database, "day_type_calendar"):
            context = f"day_type_calendar.din row {rowid}"
            day = self._parse_date(_required(row, "DAY", context), "DAY", context)
            self._day_types.setdefault(_required(row, "VERSION", context), {})[day] = \
                _required(row, "DAY_TYPE_NR", context)

        memberships: dict[tuple[str, str], set[str]] = {}
        for rowid, row in _rows(database, "day_type_2_day_attribute"):
            context = f"day_type_2_day_attribute.din row {rowid}"
            memberships.setdefault(
                (_required(row, "VERSION", context), _required(row, "DAY_TYPE_NR", context)), set()
            ).add(_required(row, "DAY_ATTRIBUTE_NR", context))

        self._eligible: dict[tuple[str, str], set[int]] = {}
        for version, days in self._day_types.items():
            for day, day_type in days.items():
                for attribute in memberships.get((version, day_type), ()):
                    self._eligible.setdefault((version, attribute), set()).add(day)

    def _read_versions(self, database: sqlite3.Connection) -> None:
        """Read the period and the priority rank of each version."""
        periods: dict[str, tuple[int, int]] = {}
        priorities: dict[str, tuple[int, int]] = {}
        for rowid, row in _rows(database, "version"):
            context = f"version.din row {rowid}"
            version = _required(row, "VERSION", context)
            days = self._day_types.get(version, {})
            bounds = []
            for field, fallback in (("PERIOD_DATE_FROM", min(days, default=0)), ("PERIOD_DATE_TO", max(days, default=0))):
                value = _optional(row, field)
                bounds.append(self._parse_date(value, field, context) if value else fallback)

            start, end = bounds
            if not start or not end:
                raise InputError(f"{context}: period is missing and day_type_calendar.din has no days for it")

            if end < start:
                raise InputError(f"{context}: period ends before it starts")

            priority = _optional(row, "PERIOD_PRIORITY")
            try:
                value = int(priority) if priority is not None else -(2**62)
            except ValueError as exc:
                raise InputError(f"{context}: invalid PERIOD_PRIORITY: {priority!r}") from exc

            # The last record of a version wins; ties between versions resolve by record order
            periods[version] = (start, end)
            priorities[version] = (value, rowid)

        self._periods = {
            version: set(range(start, end + 1)) for version, (start, end) in periods.items()
        }
        ordered = sorted(priorities, key=priorities.__getitem__)
        self._ranks = {version: rank for rank, version in enumerate(ordered)}
        self._winners: dict[tuple[str, tuple[str, ...]], set[int]] = {}

    def _read_lines(self, database: sqlite3.Connection) -> None:
        """Read which lines each version contains."""
        self._lines_by_version: dict[str, set[str]] = {}
        for rowid, row in _rows(database, "line"):
            context = f"line.din row {rowid}"
            self._lines_by_version.setdefault(_required(row, "VERSION", context), set()).add(
                _required(row, "LINE_NR", context)
            )

    def _read_trip_services(self, database: sqlite3.Connection) -> dict[ServiceKey, set[str]]:
        """Read all version, day attribute and restriction combinations used by trips with their lines."""
        result: dict[ServiceKey, set[str]] = {}
        required = ("VERSION", "LINE_NR", "DAY_ATTRIBUTE_NR")
        columns = _columns(database, "trip", required, optional=True)
        if not columns:
            return result

        restriction_column = "RESTRICTION" if "RESTRICTION" in columns else "NULL"
        for version, line, attribute, restriction in database.execute(
            f'SELECT DISTINCT VERSION, LINE_NR, DAY_ATTRIBUTE_NR, {restriction_column} FROM "dino_trip"'
        ):
            if not all(isinstance(value, str) and value.strip() for value in (version, line, attribute)):
                raise InputError("trip.din requires VERSION, LINE_NR and DAY_ATTRIBUTE_NR")

            restriction = restriction if isinstance(restriction, str) and restriction.strip() else None
            result.setdefault((version, attribute, restriction), set()).add(line)
            self._lines_by_version.setdefault(version, set()).add(line)

        return result

    def _read_restrictions(self, database: sqlite3.Connection) -> None:
        """Decode the service restriction bitmaps per version, restriction and line."""
        self._restrictions: dict[tuple[str, str], dict[str | None, set[int]]] = {}
        required = ("VERSION", "RESTRICTION", "RESTRICTION_DAYS", "DATE_FROM", "DATE_UNTIL")
        if not _columns(database, "service_restriction", required, optional=True):
            return

        for rowid, row in _rows(database, "service_restriction"):
            context = f"service_restriction.din row {rowid}"
            start = self._parse_date(_required(row, "DATE_FROM", context), "DATE_FROM", context)
            end = self._parse_date(_required(row, "DATE_UNTIL", context), "DATE_UNTIL", context)
            mask = _required(row, "RESTRICTION_DAYS", context).strip()

            first, last = date.fromordinal(start), date.fromordinal(end)
            months = (last.year - first.year) * 12 + last.month - first.month + 1
            if end < start or len(mask) != 8 * months:
                raise InputError(f"{context}: RESTRICTION_DAYS does not match DATE_FROM and DATE_UNTIL")

            days: set[int] = set()
            for index in range(months):
                year, month = divmod(first.year * 12 + first.month - 1 + index, 12)
                try:
                    decoded = decode_month_mask(mask[index * 8:index * 8 + 8], year, month + 1)
                except ValueError as exc:
                    raise InputError(f"{context}: invalid RESTRICTION_DAYS: {exc}") from exc
                days.update(day.toordinal() for day in decoded)

            key = (_required(row, "VERSION", context), _required(row, "RESTRICTION", context))
            line = _optional(row, "LINE_NR")
            self._restrictions.setdefault(key, {}).setdefault(line, set()).update(
                day for day in days if start <= day <= end
            )

    # -- date resolution -----------------------------------------------------------------------

    def _winning_dates(self, version: str, line: str) -> set[int]:
        """Return the period dates on which the version wins for a line against higher-priority versions."""
        if version not in self._periods:
            raise InputError(f"version.din has no entry for VERSION {version!r}")

        higher = tuple(sorted(
            other for other, lines in self._lines_by_version.items()
            if line in lines and other in self._ranks and self._ranks[other] > self._ranks[version]
        ))
        key = (version, higher)
        if key not in self._winners:
            dates = set(self._periods[version])
            for other in higher:
                dates -= self._periods[other]
            self._winners[key] = dates

        return self._winners[key]

    def _service_dates(self, key: ServiceKey, lines: set[str], context: str) -> set[int]:
        """Collect the active dates of a service over all of its lines."""
        version, attribute, restriction = key
        eligible = self._eligible.get((version, attribute), set())
        masks = self._restrictions.get((version, restriction)) if restriction else None
        if restriction and masks is None:
            LOGGER.warning("%s: RESTRICTION %r is missing in service_restriction.din", context, restriction)

        dates: set[int] = set()
        for line in lines:
            days = self._winning_dates(version, line) & eligible
            if restriction:
                days &= (masks or {}).get(None, set()) | (masks or {}).get(line, set())
            dates |= days

        return dates

    @staticmethod
    def _consolidate(
        identifier: str, dates: AbstractSet[int]
    ) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
        """Pick the weekly pattern of the majority and express all differences as calendar date exceptions."""
        first, last = min(dates), max(dates)
        present = [0] * 7
        total = [0] * 7
        for day in range(first, last + 1):
            weekday = date.fromordinal(day).weekday()
            total[weekday] += 1
            present[weekday] += day in dates

        active = [present[weekday] * 2 >= total[weekday] > 0 for weekday in range(7)]
        exceptions = [
            {"service_id": identifier, "date": date.fromordinal(day).strftime("%Y%m%d"),
             "exception_type": 1 if day in dates else 2}
            for day in range(first, last + 1)
            if (day in dates) != active[date.fromordinal(day).weekday()]
        ]
        if not any(active):
            return None, exceptions

        names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        return {
            "service_id": identifier, **{name: int(flag) for name, flag in zip(names, active)},
            "start_date": date.fromordinal(first).strftime("%Y%m%d"),
            "end_date": date.fromordinal(last).strftime("%Y%m%d"),
        }, exceptions
