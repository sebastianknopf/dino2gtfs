from dino2gtfs.domain.errors import CollisionError, ConverterError

def test_error_hierarchy():
    assert issubclass(CollisionError, ConverterError)
