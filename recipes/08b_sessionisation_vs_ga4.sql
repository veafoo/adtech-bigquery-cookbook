-- 08b. The same 30-minute rule on real GA4 data, compared with GA4's own session ids
-- Data: bigquery-public-data.ga4_obfuscated_sample_ecommerce, one day (2021-01-31).
-- GA4 timestamps are INT64 microseconds, so the gap test is arithmetic, not TIMESTAMP_DIFF.
-- A GA4 session is (user_pseudo_id, ga_session_id): ga_session_id alone is a start time in
-- seconds and is not unique across users. The two counts are not expected to be equal
-- (GA4 also cuts sessions at midnight, the day filter cuts others): the point is the size of
-- the gap, and that a session key without the user is wrong.

-- @query compare
WITH ev AS (
  SELECT user_pseudo_id, event_timestamp,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_20210131`
),
flags AS (
  SELECT *,
    IF(LAG(event_timestamp) OVER (PARTITION BY user_pseudo_id ORDER BY event_timestamp) IS NULL
       OR event_timestamp - LAG(event_timestamp) OVER (PARTITION BY user_pseudo_id ORDER BY event_timestamp)
          > 30 * 60 * 1000000, 1, 0) AS is_new_session
  FROM ev
)
SELECT
  SUM(is_new_session) AS custom_30min_sessions,
  COUNT(DISTINCT CONCAT(user_pseudo_id, '-', CAST(ga_session_id AS STRING))) AS ga4_sessions_user_and_id,
  COUNT(DISTINCT ga_session_id) AS ga4_session_ids_alone
FROM flags;
