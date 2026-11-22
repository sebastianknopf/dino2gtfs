"""Pure calendar primitives shared by profiles."""
from datetime import date, timedelta
from collections.abc import Iterator

def dates_between(start: date, end: date) -> Iterator[date]:
    """Yield dates in an inclusive interval."""
    if end < start:
        raise ValueError("Calendar end precedes start")
    
    current = start
    
    while current <= end:
        yield current
        current += timedelta(days=1)

def decode_month_mask(mask: str, year: int, month: int) -> set[date]:
    """Decode one DINO 8-character monthly hexadecimal bitmap."""
    if len(mask) != 8:
        raise ValueError("DINO month mask must contain eight hex characters")
    
    value = int(mask, 16)
    
    result: set[date] = set()
    for day in range(1, 32):
        if value & (1 << (day - 1)):
            result.add(date(year, month, day))
    
    return result
