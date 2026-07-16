CREATE TABLE schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE barcode_reports (
    barcode text PRIMARY KEY,
    html_gzip bytea,
    archived boolean NOT NULL DEFAULT false,
    query_slot text NOT NULL DEFAULT '',
    origin_node text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    delete_event_id uuid
);

CREATE TABLE app_entities (
    kind text NOT NULL,
    entity_key text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    origin_node text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    delete_event_id uuid,
    PRIMARY KEY (kind, entity_key)
);

CREATE TABLE operation_logs (
    id uuid PRIMARY KEY,
    category text NOT NULL,
    level text NOT NULL,
    message text NOT NULL,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    origin_node text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE sync_local_sequence;
CREATE TABLE sync_events (
    event_id uuid PRIMARY KEY,
    origin_node text NOT NULL,
    local_sequence bigint NOT NULL,
    site_epoch bigint NOT NULL,
    entity_type text NOT NULL,
    entity_key text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('upsert', 'delete')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    blob_gzip bytea,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (origin_node, local_sequence)
);

CREATE TABLE sync_cursors (
    peer_node text PRIMARY KEY,
    origin_node text NOT NULL,
    last_sequence bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sync_tombstones (
    entity_type text NOT NULL,
    entity_key text NOT NULL,
    delete_event_id uuid NOT NULL UNIQUE,
    origin_node text NOT NULL,
    deleted_at timestamptz NOT NULL,
    hk_ack_at timestamptz,
    sg_ack_at timestamptz,
    PRIMARY KEY (entity_type, entity_key)
);

CREATE INDEX barcode_reports_updated_at_idx ON barcode_reports (updated_at);
CREATE INDEX app_entities_kind_deleted_at_idx ON app_entities (kind, deleted_at);
CREATE INDEX operation_logs_category_created_at_idx ON operation_logs (category, created_at);
CREATE INDEX sync_events_origin_node_local_sequence_idx ON sync_events (origin_node, local_sequence);
