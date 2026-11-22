from dino2gtfs.profiles.dino212_default.profile import Dino212DefaultProfile

def test_profile():
    profile = Dino212DefaultProfile()
    assert profile.name == "dino212_default"
    assert profile.importer is not None and profile.converter is not None
