-- 08. Custom 30-minute sessionisation with LAG
-- The logic is right for identified users. The trap is data, not SQL: raw server logs often
-- have user_id NULL; all anonymous hits fall into one partition and CONCAT with a NULL
-- argument returns NULL, so their session id is NULL.
-- Truth: u1 has two sessions (10:00, 10:20 | 11:00 after a 40-min gap), u2 one session.
-- Two anonymous hits get no usable session id in the naive version.
-- (Timestamps here are TIMESTAMP. On GA4-style INT64 micros, TIMESTAMP_DIFF does not run:
-- compare event_timestamp - prev_timestamp > 30 * 60 * 1000000 instead, see 08b.)

-- @fixture
raw_logs AS (
  SELECT * FROM UNNEST([
    STRUCT('u1' AS user_id, TIMESTAMP '2021-01-31 10:00:00+00' AS event_timestamp),
    ('u1', TIMESTAMP '2021-01-31 10:20:00+00'),
    ('u1', TIMESTAMP '2021-01-31 11:00:00+00'),
    ('u2', TIMESTAMP '2021-01-31 10:00:00+00'),
    (NULL, TIMESTAMP '2021-01-31 10:00:00+00'),
    (NULL, TIMESTAMP '2021-01-31 10:05:00+00')
  ])
)

-- @query naive
WITH RawHits AS (
  SELECT user_id, event_timestamp,
         LAG(event_timestamp) OVER (PARTITION BY user_id ORDER BY event_timestamp) AS prev_timestamp
  FROM raw_logs
),
NewSessionFlags AS (
  SELECT *,
    IF(prev_timestamp IS NULL OR TIMESTAMP_DIFF(event_timestamp, prev_timestamp, SECOND) > 1800, 1, 0) AS is_new_session
  FROM RawHits
)
SELECT user_id, event_timestamp,
  CONCAT(user_id, '_', SUM(is_new_session) OVER (PARTITION BY user_id ORDER BY event_timestamp)) AS custom_session_id
FROM NewSessionFlags
ORDER BY user_id, event_timestamp;  -- ORDER BY added only to make the output stable

-- @query fixed
WITH RawHits AS (
  SELECT user_id, event_timestamp,
         LAG(event_timestamp) OVER (PARTITION BY user_id ORDER BY event_timestamp) AS prev_timestamp
  FROM raw_logs
  WHERE user_id IS NOT NULL  -- anonymous hits need another key (see 10), not one shared partition
    AND DATE(event_timestamp) >= DATE_SUB(DATE '2021-01-31', INTERVAL 7 DAY)  -- no full scan
),
NewSessionFlags AS (
  SELECT *,
    IF(prev_timestamp IS NULL OR TIMESTAMP_DIFF(event_timestamp, prev_timestamp, SECOND) > 1800, 1, 0) AS is_new_session
  FROM RawHits
)
SELECT user_id, event_timestamp,
  CONCAT(user_id, '_', SUM(is_new_session) OVER (PARTITION BY user_id ORDER BY event_timestamp)) AS custom_session_id
FROM NewSessionFlags
ORDER BY user_id, event_timestamp;
