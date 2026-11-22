from dino2gtfs.cli import main

def test_profiles(capsys):
    assert main(["profiles"]) == 0
    assert "dino212_default" in capsys.readouterr().out
