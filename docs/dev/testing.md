# Testing

Every implemented Python module has a focused smoke/unit test. Tests use small in-memory examples and temporary ZIP archives; external fixtures are not required.

```bash
python -m pytest
```

The initial tests assert wiring, parsing, cleanup, deterministic primitives and the deliberate unimplemented-conversion error. They do not assert that a complete GTFS feed is generated.
