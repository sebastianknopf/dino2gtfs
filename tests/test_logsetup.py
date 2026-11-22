import logging
from dino2gtfs.infrastructure.logsetup import configure_logging

def test_logging_format():
    configure_logging()
    assert "[%(levelname)s]: %(message)s" in logging.getLogger().handlers[0].formatter._fmt
