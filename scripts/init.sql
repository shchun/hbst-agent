CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS matzip (
    id SERIAL PRIMARY KEY,
    seq INTEGER,
    name VARCHAR(255) NOT NULL,
    address TEXT,
    memo TEXT,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    registered_at TIMESTAMPTZ,
    location GEOMETRY(Point, 4326) GENERATED ALWAYS AS (
        ST_SetSRID(ST_MakePoint(lng, lat), 4326)
    ) STORED
);

CREATE INDEX IF NOT EXISTS matzip_location_idx ON matzip USING GIST (location);
