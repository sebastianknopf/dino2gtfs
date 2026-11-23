from dino2gtfs.application.interfaces import Profile
from dino2gtfs.profiles.dino212_default.profile import Dino212DefaultProfile

def test_profile_protocol_shape():
    profile = Dino212DefaultProfile()
    assert hasattr(profile, "converter")
    assert not hasattr(profile, "importer")
