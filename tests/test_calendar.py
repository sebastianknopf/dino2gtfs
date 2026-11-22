from datetime import date
from dino2gtfs.domain.calendar import dates_between, decode_month_mask

def test_dates_between():
    assert list(dates_between(date(2026, 1, 1), date(2026, 1, 2))) == [date(2026, 1, 1), date(2026, 1, 2)]

def test_month_mask():
    assert decode_month_mask("00000005", 2026, 1) == {date(2026, 1, 1), date(2026, 1, 3)}
