-- Minimal copy of the orchestrator app schema: only the tables and columns the what-if card
-- reads. Names and types are copied from the orchestrator migrations (V2, V3, V38, V52, V98,
-- V130, V153). No FKs.

CREATE TABLE task (
    status       varchar   NOT NULL,
    amount       bigint    NOT NULL,
    succeeded_at timestamp NULL
);
CREATE INDEX idx_task_succeeded ON task (succeeded_at);

CREATE TABLE quota_ledger (
    id           bigserial PRIMARY KEY,
    subject_type varchar   NOT NULL,
    subject_id   uuid      NOT NULL,
    amount       bigint    NOT NULL,
    ts           timestamp NOT NULL,
    status       varchar   NOT NULL
);
CREATE INDEX idx_quota_ledger_subject ON quota_ledger (subject_type, subject_id, ts) INCLUDE (amount);

CREATE TABLE account_cooldown (
    id         bigserial PRIMARY KEY,
    account_id uuid      NOT NULL,
    reason     text      NOT NULL,
    applied_at timestamp NOT NULL DEFAULT now(),
    until      timestamp NULL,
    cleared_at timestamp NULL
);

CREATE TABLE balance_ledger (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    number_id     uuid        NOT NULL,
    delta         bigint      NOT NULL,
    balance_after bigint      NOT NULL,
    source        text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_balance_ledger_number ON balance_ledger (number_id, created_at);
CREATE INDEX idx_balance_ledger_source ON balance_ledger (source, created_at);

CREATE TABLE phone_provider (
    code text PRIMARY KEY,
    mode text NOT NULL
);

CREATE TABLE phone_provider_deposit_ledger (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_code text        NOT NULL,
    kind          text        NOT NULL,
    amount        bigint      NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_ppdl_provider_created ON phone_provider_deposit_ledger (provider_code, created_at);

CREATE TABLE account_number_tenure (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid        NOT NULL,
    number_id  uuid        NOT NULL,
    source     text        NOT NULL DEFAULT 'TEST',
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to   timestamptz NULL
);
CREATE UNIQUE INDEX uq_account_number_tenure_open ON account_number_tenure (account_id) WHERE valid_to IS NULL;
CREATE INDEX idx_account_number_tenure_account_from ON account_number_tenure (account_id, valid_from DESC);
CREATE INDEX idx_account_number_tenure_number_from ON account_number_tenure (number_id, valid_from DESC);

CREATE TABLE number_provider_tenure (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    number_id     uuid        NOT NULL,
    provider_code text        NOT NULL,
    source        text        NOT NULL DEFAULT 'TEST',
    valid_from    timestamptz NOT NULL DEFAULT now(),
    valid_to      timestamptz NULL
);
CREATE UNIQUE INDEX uq_number_provider_tenure_open ON number_provider_tenure (number_id) WHERE valid_to IS NULL;
CREATE INDEX idx_number_provider_tenure_number_from ON number_provider_tenure (number_id, valid_from DESC);

CREATE TABLE device_state_tenure (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id         uuid        NOT NULL,
    state             varchar     NOT NULL,
    validation_status text        NOT NULL,
    held_at           timestamptz NULL,
    agent_id          uuid        NULL,
    agent_status      varchar     NULL,
    agent_disabled_at timestamptz NULL,
    source            text        NOT NULL,
    origin_id         text        NULL,
    valid_from        timestamptz NOT NULL DEFAULT now(),
    valid_to          timestamptz NULL
);
CREATE UNIQUE INDEX uq_device_state_tenure_open ON device_state_tenure (device_id) WHERE valid_to IS NULL;
CREATE INDEX idx_device_state_tenure_device_from ON device_state_tenure (device_id, valid_from DESC);

CREATE TABLE account_state_tenure (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id        uuid        NOT NULL,
    validation_status text        NOT NULL,
    archived_at       timestamptz NULL,
    device_id         uuid        NOT NULL,
    source            text        NOT NULL,
    origin_id         text        NULL,
    valid_from        timestamptz NOT NULL DEFAULT now(),
    valid_to          timestamptz NULL
);
CREATE UNIQUE INDEX uq_account_state_tenure_open ON account_state_tenure (account_id) WHERE valid_to IS NULL;
CREATE INDEX idx_account_state_tenure_account_from ON account_state_tenure (account_id, valid_from DESC);

-- Scenario helpers: spine_h(k) = start of the hourly Moscow bin k hours before the current one.
CREATE FUNCTION spine_h(k int) RETURNS timestamptz LANGUAGE sql STABLE AS $$
    SELECT date_trunc('hour', now(), 'Europe/Moscow') - k * INTERVAL '1 hour'
$$;
CREATE FUNCTION spine_h_utc(k int) RETURNS timestamp LANGUAGE sql STABLE AS $$
    SELECT spine_h(k) AT TIME ZONE 'UTC'
$$;
