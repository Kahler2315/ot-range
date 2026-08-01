-- Cedar Hollow process historian schema.
--
-- One wide row per poll rather than a narrow (tag, ts, value) table:
-- simpler to write, and every Grafana panel here is "plot these columns
-- against ts", which a wide table does with a single SELECT and no
-- pivoting. Revisit if the point count grows enough that this becomes
-- unwieldy — it won't for one process.

CREATE TABLE IF NOT EXISTS process_history (
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    level_pct       REAL NOT NULL,
    flow_lpm        REAL NOT NULL,
    chlorine_mgl    REAL NOT NULL,
    pump_current_a  REAL NOT NULL,
    pump_run        BOOLEAN NOT NULL,
    valve_open      BOOLEAN NOT NULL,
    cl_run          BOOLEAN NOT NULL,
    lshh            BOOLEAN NOT NULL,
    lsll            BOOLEAN NOT NULL,
    pump_fault      BOOLEAN NOT NULL,
    alarm           BOOLEAN NOT NULL,
    mode_auto       BOOLEAN NOT NULL
);

-- Every panel in the starter dashboard filters/orders on ts, and this is
-- append-only time-series data, so a plain btree on ts covers the
-- access pattern without needing anything more exotic (a real
-- TimescaleDB hypertable would be the next step if retention/volume
-- ever became a real concern for this teaching-scale range).
CREATE INDEX IF NOT EXISTS process_history_ts_idx ON process_history (ts);
