-- Prod-like volume for the what-if card, relative to spine_h(0): 14 days of history.
-- 500 devices (~10k device journal rows/day), 2,000 accounts on 1,000 numbers (beeline DEFAULT /
-- mts DEPOSIT), 4,500 SUCCESS tasks/day with ledger rows, 200 cooldowns/day.
-- Requires schema.sql loaded first.
INSERT INTO phone_provider (code, mode) VALUES ('beeline', 'DEFAULT'), ('mts', 'DEPOSIT');

-- Device journal: 280 rows per device over 14 days (one change every 72 min), last row open.
INSERT INTO device_state_tenure (device_id, state, validation_status, held_at, agent_status,
                                 source, valid_from, valid_to)
SELECT md5('dev' || d)::uuid,
       (ARRAY['IDLE', 'BUSY', 'OFFLINE', 'IDLE', 'IDLE'])[1 + (d + i) % 5],
       'VALIDATED', NULL, 'ONLINE',
       CASE WHEN i = 0 THEN 'SEED' ELSE 'SNAPSHOT' END,
       spine_h(336) + i * INTERVAL '72 minutes',
       CASE WHEN i = 279 THEN NULL ELSE spine_h(336) + (i + 1) * INTERVAL '72 minutes' END
FROM generate_series(0, 499) d, generate_series(0, 279) i;

INSERT INTO account_state_tenure (account_id, validation_status, device_id, source, valid_from)
SELECT md5('acc' || a)::uuid, 'GOOD', md5('dev' || (a % 500))::uuid, 'SEED', spine_h(336)
FROM generate_series(0, 1999) a;

INSERT INTO account_number_tenure (account_id, number_id, valid_from)
SELECT md5('acc' || a)::uuid, md5('num' || (a / 2))::uuid, spine_h(336)
FROM generate_series(0, 1999) a;

INSERT INTO number_provider_tenure (number_id, provider_code, valid_from)
SELECT md5('num' || n)::uuid, CASE WHEN n < 500 THEN 'beeline' ELSE 'mts' END, spine_h(336)
FROM generate_series(0, 999) n;

-- Tasks: 63,000 over 14 days. Each has an ACCOUNT and a NUMBER quota row; DEFAULT numbers get a
-- balance row (a top-up every 25th), DEPOSIT numbers a deposit ledger row (a credit every 50th).
CREATE TEMP TABLE gen AS
SELECT g,
       1000 + (g % 7) * 250                                       AS amount,
       spine_utc.ts - (g * INTERVAL '20 seconds') * 1             AS ts,
       g % 2000                                                   AS a
FROM generate_series(1, 63000) g,
     (SELECT spine_h_utc(0) AS ts) spine_utc;

INSERT INTO task (status, amount, succeeded_at) SELECT 'SUCCESS', amount, ts FROM gen;
INSERT INTO quota_ledger (subject_type, subject_id, amount, ts, status)
SELECT 'ACCOUNT', md5('acc' || a)::uuid, amount, ts, 'COMMITTED' FROM gen;
INSERT INTO quota_ledger (subject_type, subject_id, amount, ts, status)
SELECT 'NUMBER', md5('num' || (a / 2))::uuid, amount, ts, 'COMMITTED' FROM gen;

INSERT INTO balance_ledger (number_id, delta, balance_after, source, created_at)
SELECT md5('num' || (a / 2))::uuid,
       CASE WHEN g % 25 = 0 THEN 20000 ELSE -amount END,
       5000000 + g,
       CASE WHEN g % 25 = 0 THEN 'PROVIDER_TOPUP' ELSE 'TASK' END,
       ts AT TIME ZONE 'UTC'
FROM gen WHERE a / 2 < 500;

INSERT INTO phone_provider_deposit_ledger (provider_code, kind, amount, created_at)
SELECT 'mts', CASE WHEN g % 50 = 0 THEN 'DEPOSIT' ELSE 'TASK' END,
       CASE WHEN g % 50 = 0 THEN 100000 ELSE amount END, ts AT TIME ZONE 'UTC'
FROM gen WHERE a / 2 >= 500;

-- 200 cooldowns per day (2,800), 6 hours each, spread over the accounts.
INSERT INTO account_cooldown (account_id, reason, applied_at, until)
SELECT md5('acc' || (c % 2000))::uuid, 'APPLE_VELOCITY',
       spine_h_utc(0) - c * INTERVAL '7200 seconds' / 1, spine_h_utc(0) - c * INTERVAL '7200 seconds' / 1 + INTERVAL '6 hours'
FROM generate_series(1, 2800) c;

ANALYZE;
