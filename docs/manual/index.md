# User Manual

dino2gtfs imports a DINO ZIP, converts it using a selected profile and writes a GTFS ZIP. Start with the [sample configuration](../../config/default.yaml) and run:

```bash
dino2gtfs convert --profile dino212_default --config config/default.yaml --input input/dino.zip --output output/gtfs.zip
```

Use `dino2gtfs profiles` to list profiles. Add `--database output/database.db3` to keep the staging database for inspection; an existing file at that path is replaced.

The default profile currently converts agencies only. Other GTFS tables are exported empty, so the resulting feed is not a complete, validated timetable.

## Configuration

- [GTFS ID Templates](ids.md): configure identifiers and use the fingerprint placeholder.
