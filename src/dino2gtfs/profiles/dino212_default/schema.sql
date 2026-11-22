-- Initial staging schema. Field-level DINO tables are a future implementation.
CREATE TABLE IF NOT EXISTS source_rows (
    source_file TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (source_file, source_row)
);
