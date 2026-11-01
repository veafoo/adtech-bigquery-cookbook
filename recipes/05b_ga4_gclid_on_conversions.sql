-- 05b. Getting the gclid onto GA4 conversion events (fixture with the GA4 export schema)
-- The documented field is collected_traffic_source.gclid, filled on the event where the
-- traffic source was collected (typically the landing hit), not on the purchase.
-- (The public 2020-2021 sample has no collected_traffic_source field, hence the fixture.)
-- Truth: u1 landed with gclid GCLID-A then bought in the same session -> its purchase should
-- carry GCLID-A. u2 started a session in the SAME second (same ga_session_id) without a gclid.
-- Naive trap: reading 'gclid' from event_params of the purchase returns NULL on every row.
-- Fix: carry the session's last non-NULL gclid forward, partitioned by user AND session id
-- (ga_session_id is a start time in seconds, not unique across users).

-- @fixture
events AS (
  SELECT * FROM UNNEST([
    STRUCT(1000000 AS event_timestamp, 'u1' AS user_pseudo_id, 'page_view' AS event_name,
           [STRUCT('ga_session_id' AS key, STRUCT(CAST(NULL AS STRING) AS string_value, 1611900000 AS int_value) AS value),
            STRUCT('page_location', STRUCT('https://shop.example/?gclid=GCLID-A', NULL))] AS event_params,
           STRUCT('GCLID-A' AS gclid) AS collected_traffic_source),
    (2000000, 'u1', 'purchase',
           [STRUCT('ga_session_id' AS key, STRUCT(CAST(NULL AS STRING) AS string_value, 1611900000 AS int_value) AS value),
            STRUCT('page_location', STRUCT('https://shop.example/thanks', NULL))],
           STRUCT(CAST(NULL AS STRING) AS gclid)),
    (1500000, 'u2', 'page_view',
           [STRUCT('ga_session_id' AS key, STRUCT(CAST(NULL AS STRING) AS string_value, 1611900000 AS int_value) AS value),
            STRUCT('page_location', STRUCT('https://shop.example/', NULL))],
           STRUCT(CAST(NULL AS STRING) AS gclid)),
    (2500000, 'u2', 'purchase',
           [STRUCT('ga_session_id' AS key, STRUCT(CAST(NULL AS STRING) AS string_value, 1611900000 AS int_value) AS value),
            STRUCT('page_location', STRUCT('https://shop.example/thanks', NULL))],
           STRUCT(CAST(NULL AS STRING) AS gclid))
  ])
)

-- @query naive
SELECT user_pseudo_id, event_name,
       (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'gclid') AS gclid
FROM events
WHERE event_name IN ('purchase', 'generate_lead')
ORDER BY user_pseudo_id;

-- @query fixed
WITH ev AS (
  SELECT event_timestamp, user_pseudo_id, event_name,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
    collected_traffic_source.gclid AS gclid
  FROM events
)
SELECT user_pseudo_id, event_name,
  LAST_VALUE(gclid IGNORE NULLS) OVER (
    PARTITION BY user_pseudo_id, ga_session_id ORDER BY event_timestamp
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS session_gclid
FROM ev
QUALIFY event_name IN ('purchase', 'generate_lead')
ORDER BY user_pseudo_id;
