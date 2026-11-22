from dino2gtfs.cli import build_parser

def test_main_entrypoint_parser():
    assert build_parser().prog == "dino2gtfs"
