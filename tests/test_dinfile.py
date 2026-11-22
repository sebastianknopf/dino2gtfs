from io import StringIO
from dino2gtfs.infrastructure.dinfile import read_din

def test_quoted_delimiter():
    assert list(read_din(StringIO('ID;NAME\n1;"A;B"\n'))) == [{"ID": "1", "NAME": "A;B"}]
