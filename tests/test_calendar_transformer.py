import logging
from datetime import date, timedelta

import pytest

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.infrastructure.sqlite import temporary_database
from dino2gtfs.transformers.calendar import CalendarTransformer


SETTINGS = {"ids": {"calendar": {"mode": "template", "template": "service:{gtfs.canonical}"}}}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def transform(db, settings=SETTINGS):
    transformer = CalendarTransformer()
    transformer.initialize(db, settings)
    transformer.transform(db, settings)


def day_range(start, days):
    first = date(*start)
    return [first + timedelta(days=offset) for offset in range(days)]


def create_tables(db, trip_columns=None):
    db.execute("CREATE TABLE dino_version (VERSION TEXT, PERIOD_DATE_FROM TEXT, PERIOD_DATE_TO TEXT, PERIOD_PRIORITY TEXT)")
    db.execute("CREATE TABLE dino_day_type_calendar (VERSION TEXT, DAY TEXT, DAY_TYPE_NR TEXT)")
    db.execute("CREATE TABLE dino_day_type_2_day_attribute (VERSION TEXT, DAY_TYPE_NR TEXT, DAY_ATTRIBUTE_NR TEXT)")
    db.execute("CREATE TABLE dino_day_attribute (VERSION TEXT, DAY_ATTRIBUTE_NR TEXT, STR_DAY_ATTRIBUTE TEXT)")
    db.execute("CREATE TABLE dino_line (VERSION TEXT, LINE_NR TEXT)")
    if trip_columns:
        db.execute(f"CREATE TABLE dino_trip ({trip_columns})")


def add_version(db, version, start, days, priority="", attributes=(("A1", ""),), lines=("L",), weekdays_only=True):
    """Add a version; day type 1 covers Monday to Friday, day type 2 the weekend, A1 belongs to type 1."""
    dates = day_range(start, days)
    end = dates[-1]
    db.execute("INSERT INTO dino_version VALUES (?, ?, ?, ?)", (
        version, dates[0].strftime("%Y%m%d"), end.strftime("%Y%m%d"), priority,
    ))
    db.executemany("INSERT INTO dino_day_type_calendar VALUES (?, ?, ?)", [
        (version, day.strftime("%Y%m%d"), "1" if day.weekday() < 5 else "2") for day in dates
    ])
    db.execute("INSERT INTO dino_day_type_2_day_attribute VALUES (?, '1', 'A1')", (version,))
    db.executemany("INSERT INTO dino_day_attribute VALUES (?, ?, ?)", [(version, nr, text) for nr, text in attributes])
    db.executemany("INSERT INTO dino_line VALUES (?, ?)", [(version, line) for line in lines])


def expand(db, service_id):
    """Rebuild the exact date set of a service from calendar.txt and calendar_dates.txt rows."""
    dates = set()
    row = db.execute(
        f"SELECT {', '.join(WEEKDAYS)}, start_date, end_date FROM gtfs_calendar WHERE service_id = ?", (service_id,)
    ).fetchone()
    if row is not None:
        start = date.fromisoformat("-".join((row[7][:4], row[7][4:6], row[7][6:])))
        end = date.fromisoformat("-".join((row[8][:4], row[8][4:6], row[8][6:])))
        current = start
        while current <= end:
            if row[current.weekday()]:
                dates.add(current)
            current += timedelta(days=1)
    for text, exception in db.execute(
        "SELECT date, exception_type FROM gtfs_calendar_dates WHERE service_id = ?", (service_id,)
    ):
        day = date(int(text[:4]), int(text[4:6]), int(text[6:]))
        dates.add(day) if exception == 1 else dates.remove(day)

    return dates


def weekdays_in(start, days):
    return {day for day in day_range(start, days) if day.weekday() < 5}


def test_regular_service_uses_calendar_txt_only():
    with temporary_database() as db:
        create_tables(db)
        add_version(db, "1", (2026, 1, 5), 14)
        transform(db)

        assert db.execute(f"SELECT {', '.join(WEEKDAYS)}, start_date, end_date FROM gtfs_calendar").fetchall() == [
            (1, 1, 1, 1, 1, 0, 0, "20260105", "20260116")
        ]
        assert db.execute("SELECT * FROM gtfs_calendar_dates").fetchall() == []
        assert db.execute("SELECT * FROM map_calendar_2_calendar").fetchall() == [("1", "A1", None, "service:1:A1")]


def test_day_attribute_text_extends_canonical_id():
    with temporary_database() as db:
        create_tables(db)
        add_version(db, "1", (2026, 1, 5), 7, attributes=(("A1", "Mo-Fr"), ("A2", "")))
        transform(db)

        assert db.execute("SELECT VERSION, DAY_ATTRIBUTE_NR, gtfs_id FROM map_calendar_2_calendar ORDER BY 2").fetchall() == [
            ("1", "A1", "service:1:A1:Mo-Fr"), ("1", "A2", "service:1:A2"),
        ]


def test_exceptions_preserve_exact_dates_and_empty_services_keep_their_mapping(caplog):
    with temporary_database() as db:
        create_tables(db)
        add_version(db, "1", (2026, 1, 5), 14, attributes=(("A1", ""), ("A2", "")))
        # Wednesday 2026-01-07 becomes a weekend-like day without service for A1
        db.execute("UPDATE dino_day_type_calendar SET DAY_TYPE_NR = '2' WHERE DAY = '20260107'")
        with caplog.at_level(logging.WARNING):
            transform(db)

        assert expand(db, "service:1:A1") == weekdays_in((2026, 1, 5), 14) - {date(2026, 1, 7)}
        assert db.execute("SELECT date, exception_type FROM gtfs_calendar_dates").fetchall() == [("20260107", 2)]
        assert db.execute("SELECT COUNT(*) FROM gtfs_calendar WHERE service_id = 'service:1:A2'").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM map_calendar_2_calendar").fetchone() == (2,)
        assert any(
            record.levelno >= logging.WARNING and "has no active dates" in record.getMessage()
            for record in caplog.records
        )


def test_services_with_identical_dates_share_one_service_id_and_keep_their_mappings():
    with temporary_database() as db:
        create_tables(db)
        add_version(db, "1", (2026, 1, 5), 14, attributes=(("A1", ""), ("A2", ""), ("A3", "")))
        db.execute("INSERT INTO dino_day_type_2_day_attribute VALUES ('1', '1', 'A2')")
        db.execute("INSERT INTO dino_day_type_2_day_attribute VALUES ('1', '2', 'A3')")
        transform(db)

        assert db.execute(
            "SELECT DAY_ATTRIBUTE_NR, gtfs_id FROM map_calendar_2_calendar ORDER BY DAY_ATTRIBUTE_NR"
        ).fetchall() == [("A1", "service:1:A1"), ("A2", "service:1:A1"), ("A3", "service:1:A3")]
        assert db.execute("SELECT service_id FROM gtfs_calendar ORDER BY service_id").fetchall() == [
            ("service:1:A1",), ("service:1:A3",)
        ]
        assert expand(db, "service:1:A1") == weekdays_in((2026, 1, 5), 14)


def test_sparse_service_stays_exact():
    with temporary_database() as db:
        create_tables(db)
        add_version(db, "1", (2026, 1, 5), 28)
        db.execute("UPDATE dino_day_type_calendar SET DAY_TYPE_NR = '2' WHERE DAY NOT IN ('20260107', '20260121', '20260122')")
        transform(db)

        assert expand(db, "service:1:A1") == {date(2026, 1, 7), date(2026, 1, 21), date(2026, 1, 22)}


def test_higher_priority_version_replaces_shared_lines_only():
    with temporary_database() as db:
        create_tables(db)
        add_version(db, "1", (2026, 1, 5), 28, priority="1", lines=("L", "M"))
        add_version(db, "2", (2026, 1, 19), 14, priority="2", lines=("L",))
        transform(db)

        # Line M exists only in version 1 and keeps the overlap, so version 1 stays active for it
        assert expand(db, "service:1:A1") == weekdays_in((2026, 1, 5), 28)

    with temporary_database() as db:
        create_tables(db)
        add_version(db, "1", (2026, 1, 5), 28, priority="1")
        add_version(db, "2", (2026, 1, 19), 14, priority="2")
        transform(db)

        assert expand(db, "service:1:A1") == weekdays_in((2026, 1, 5), 14)
        assert expand(db, "service:2:A1") == weekdays_in((2026, 1, 19), 14)


@pytest.mark.parametrize("priorities,winner", [(("", ""), "2"), (("5", "5"), "2"), (("5", "1"), "1")])
def test_equal_or_missing_priority_prefers_the_last_version(priorities, winner):
    with temporary_database() as db:
        create_tables(db)
        add_version(db, "1", (2026, 1, 5), 14, priority=priorities[0])
        add_version(db, "2", (2026, 1, 5), 14, priority=priorities[1])
        transform(db)

        losing = "1" if winner == "2" else "2"
        assert expand(db, f"service:{winner}:A1") == weekdays_in((2026, 1, 5), 14)
        assert db.execute("SELECT COUNT(*) FROM gtfs_calendar WHERE service_id = ?", (f"service:{losing}:A1",)).fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM map_calendar_2_calendar").fetchone() == (2,)


def test_restriction_services_are_built_from_trip_combinations():
    with temporary_database() as db:
        create_tables(db, "VERSION TEXT, LINE_NR TEXT, DAY_ATTRIBUTE_NR TEXT, RESTRICTION TEXT")
        add_version(db, "1", (2026, 1, 1), 31)
        db.execute(
            "CREATE TABLE dino_service_restriction "
            "(VERSION TEXT, RESTRICTION TEXT, RESTRICTION_DAYS TEXT, DATE_FROM TEXT, DATE_UNTIL TEXT, LINE_NR TEXT)"
        )
        # Bits 4 and 11 select Monday 2026-01-05 and Monday 2026-01-12
        db.execute("INSERT INTO dino_service_restriction VALUES ('1', 'aA', '00000810', '20260101', '20260131', 'L')")
        db.executemany("INSERT INTO dino_trip VALUES (?, ?, ?, ?)", [
            ("1", "L", "A1", ""), ("1", "L", "A1", "aA"), ("1", "L", "A1", "aA"),
        ])
        transform(db)

        assert db.execute(
            "SELECT VERSION, DAY_ATTRIBUTE_NR, RESTRICTION, gtfs_id FROM map_calendar_2_calendar ORDER BY gtfs_id"
        ).fetchall() == [("1", "A1", None, "service:1:A1"), ("1", "A1", "aA", "service:1:A1:aA")]
        assert expand(db, "service:1:A1") == weekdays_in((2026, 1, 1), 31)
        assert expand(db, "service:1:A1:aA") == {date(2026, 1, 5), date(2026, 1, 12)}


def test_trip_with_unknown_day_attribute_fails():
    with temporary_database() as db:
        create_tables(db, "VERSION TEXT, LINE_NR TEXT, DAY_ATTRIBUTE_NR TEXT")
        add_version(db, "1", (2026, 1, 5), 7)
        db.execute("INSERT INTO dino_trip VALUES ('1', 'L', 'X')")
        with pytest.raises(InputError, match="DAY_ATTRIBUTE_NR 'X'"):
            transform(db)


def test_template_collision_is_fatal():
    settings = {"ids": {"calendar": {"mode": "template", "template": "service:{day_attribute.VERSION}"}}}
    with temporary_database() as db:
        create_tables(db)
        add_version(db, "1", (2026, 1, 5), 7, attributes=(("A1", ""), ("A2", "")))
        with pytest.raises(CollisionError, match="Identity collision in gtfs_calendar: 'service:1'"):
            transform(db, settings)


def test_global_mode_is_rejected():
    with temporary_database() as db:
        create_tables(db)
        add_version(db, "1", (2026, 1, 5), 7)
        with pytest.raises(ConfigurationError, match="ids.calendar.mode must be template"):
            transform(db, {"ids": {"calendar": {"mode": "global", "template": "service:{gtfs.canonical}"}}})
