-- 10. Back-filling a missing pseudo id with LAST_VALUE IGNORE NULLS
-- Truth: two people share the same IP + user agent on 2021-01-31 (an office, a household):
-- visitor A in the morning (09:00-09:10), visitor B in the afternoon (15:00-15:05).
-- Each anonymous hit should take the id of its own 30-minute session; rows that already have
-- an id keep it.
-- Naive traps: the result replaces EVERY row, valid ids included, with the last id of the
-- partition; the partition is IP + UA over a whole day, so A's rows become B.
-- Compliance note: rebuilding an IP + UA key on hits where consent was denied, then tying them
-- back to an id, is the kind of re-identification Consent Mode exists to avoid. This recipe
-- shows the SQL mechanics only.

-- @fixture
raw_events AS (
  SELECT * FROM UNNEST([
    STRUCT(TIMESTAMP '2021-01-31 09:00:00+00' AS event_timestamp, 'A' AS user_pseudo_id, '10.0.0.1' AS client_ip, 'UA-1' AS user_agent),
    (TIMESTAMP '2021-01-31 09:05:00+00', NULL, '10.0.0.1', 'UA-1'),
    (TIMESTAMP '2021-01-31 09:10:00+00', 'A',  '10.0.0.1', 'UA-1'),
    (TIMESTAMP '2021-01-31 15:00:00+00', NULL, '10.0.0.1', 'UA-1'),
    (TIMESTAMP '2021-01-31 15:05:00+00', 'B',  '10.0.0.1', 'UA-1'),
    (TIMESTAMP '2021-01-31 10:00:00+00', NULL, '10.0.0.2', 'UA-2')
  ])
)

-- @query naive
WITH RawSession AS (
  SELECT event_timestamp, user_pseudo_id, CONCAT(client_ip, '_', user_agent) AS server_session_hash
  FROM raw_events
  WHERE DATE(event_timestamp) = DATE '2021-01-31'
)
SELECT event_timestamp, user_pseudo_id AS original_id,
  LAST_VALUE(user_pseudo_id IGNORE NULLS) OVER (
    PARTITION BY server_session_hash ORDER BY event_timestamp
    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS stitched_pseudo_id
FROM RawSession
ORDER BY event_timestamp;

-- @query fixed
WITH RawSession AS (
  SELECT event_timestamp, user_pseudo_id, CONCAT(client_ip, '_', user_agent) AS server_session_hash
  FROM raw_events
  WHERE DATE(event_timestamp) = DATE '2021-01-31'
),
Gaps AS (
  SELECT *,
    IF(TIMESTAMP_DIFF(event_timestamp,
         LAG(event_timestamp) OVER (PARTITION BY server_session_hash ORDER BY event_timestamp), SECOND) > 1800
       OR LAG(event_timestamp) OVER (PARTITION BY server_session_hash ORDER BY event_timestamp) IS NULL,
       1, 0) AS is_new_session
  FROM RawSession
),
RawSessionWithGapCounter AS (
  SELECT *, SUM(is_new_session) OVER (PARTITION BY server_session_hash ORDER BY event_timestamp) AS session_n
  FROM Gaps
)
SELECT event_timestamp, user_pseudo_id AS original_id,
  COALESCE(user_pseudo_id,
    LAST_VALUE(user_pseudo_id IGNORE NULLS) OVER (
      PARTITION BY server_session_hash, session_n ORDER BY event_timestamp
      ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)) AS stitched_pseudo_id
FROM RawSessionWithGapCounter
ORDER BY event_timestamp;
