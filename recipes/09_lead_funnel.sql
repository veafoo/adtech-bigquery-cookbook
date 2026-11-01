-- 09. Time from add_to_cart to purchase with LEAD (public dataset)
-- Data: bigquery-public-data.ga4_obfuscated_sample_ecommerce, January 2021.
-- clean_events is built from the export: one row per event with ga_session_id and a TIMESTAMP.
-- Naive traps: PARTITION BY ga_session_id alone mixes users who started a session in the same
-- second (one user's add_to_cart paired with another user's purchase); TIMESTAMP_DIFF(..., MINUTE)
-- counts whole minute boundaries, so the average is biased.
-- The WHERE runs before the window functions, so other events cannot sit between the two steps.

-- @fixture
clean_events AS (
  SELECT user_pseudo_id, event_name,
    TIMESTAMP_MICROS(event_timestamp) AS event_timestamp,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20210101' AND '20210131'
)

-- @query naive
WITH Step1 AS (
  SELECT ga_session_id, event_name, event_timestamp,
    LEAD(event_name) OVER (PARTITION BY ga_session_id ORDER BY event_timestamp) AS next_event,
    LEAD(event_timestamp) OVER (PARTITION BY ga_session_id ORDER BY event_timestamp) AS next_timestamp
  FROM clean_events
  WHERE event_name IN ('add_to_cart', 'purchase')
)
SELECT ROUND(AVG(TIMESTAMP_DIFF(next_timestamp, event_timestamp, MINUTE)), 3) AS avg_minutes_to_purchase,
       COUNT(*) AS pairs  -- pair count added to compare with the fix
FROM Step1
WHERE event_name = 'add_to_cart' AND next_event = 'purchase';

-- @query fixed
WITH Step1 AS (
  SELECT user_pseudo_id, ga_session_id, event_name, event_timestamp,
    LEAD(event_name)      OVER w AS next_event,
    LEAD(event_timestamp) OVER w AS next_timestamp
  FROM clean_events
  WHERE event_name IN ('add_to_cart', 'purchase')
  WINDOW w AS (PARTITION BY user_pseudo_id, ga_session_id ORDER BY event_timestamp)
)
SELECT ROUND(AVG(TIMESTAMP_DIFF(next_timestamp, event_timestamp, SECOND) / 60), 3) AS avg_minutes_to_purchase,
       COUNT(*) AS pairs
FROM Step1
WHERE event_name = 'add_to_cart' AND next_event = 'purchase';

-- @query shared_session_ids
-- how many ga_session_id values are shared by more than one user in the month
SELECT COUNT(*) AS session_ids, COUNTIF(users > 1) AS shared_by_several_users
FROM (SELECT ga_session_id, COUNT(DISTINCT user_pseudo_id) AS users
      FROM clean_events GROUP BY ga_session_id);
