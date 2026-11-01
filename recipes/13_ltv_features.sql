-- 13. Feature table for a 90-day LTV model (public dataset)
-- Data: bigquery-public-data.ga4_obfuscated_sample_ecommerce, 2020-11-01..2021-01-31 (92 days),
-- treated as the whole history; "now" is the end of 2021-01-31.
-- Naive traps: the label sums ALL purchases ever, not the 90 days after the first visit; users
-- first seen recently have not had 90 days to buy (right-censored label, biased toward 0).
-- Fix: keep users with a full 90-day window observed, and count purchases inside it.
-- Output is summarised (users, zero-label share, mean label) to keep the frozen result small.
-- No CREATE MODEL here: the sandbox can create models, but the recipe is about the label.

-- @fixture
events AS (
  SELECT * FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
)

-- @query naive
WITH FirstVisit AS (
  SELECT user_pseudo_id, MIN(event_timestamp) AS first_seen_time
  FROM events GROUP BY 1
),
DayOneBehavior AS (
  SELECT e.user_pseudo_id,
    COUNT(DISTINCT (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location')) AS unique_pages_viewed,
    COUNTIF(e.event_name = 'add_to_cart') AS cart_adds_day_1,
    MAX(e.event_timestamp) - MIN(e.event_timestamp) AS day_1_duration_micros
  FROM events e
  INNER JOIN FirstVisit f ON e.user_pseudo_id = f.user_pseudo_id
  WHERE e.event_timestamp <= f.first_seen_time + 86400000000
  GROUP BY 1
),
FutureLTV AS (
  SELECT user_pseudo_id, SUM(ecommerce.purchase_revenue) AS actual_90d_ltv
  FROM events WHERE event_name = 'purchase' GROUP BY 1
),
Training AS (
  SELECT b.user_pseudo_id, b.unique_pages_viewed, b.cart_adds_day_1, b.day_1_duration_micros,
         COALESCE(l.actual_90d_ltv, 0) AS target_ltv
  FROM DayOneBehavior b LEFT JOIN FutureLTV l ON b.user_pseudo_id = l.user_pseudo_id
)
SELECT COUNT(*) AS users, COUNTIF(target_ltv = 0) AS zero_label_users,
       ROUND(AVG(target_ltv), 4) AS mean_label, ROUND(SUM(target_ltv), 2) AS sum_label
FROM Training;

-- @query fixed
WITH FirstVisit AS (
  SELECT user_pseudo_id, MIN(event_timestamp) AS first_seen_time
  FROM events GROUP BY 1
  HAVING MIN(event_timestamp) <= UNIX_MICROS(TIMESTAMP_SUB(TIMESTAMP '2021-02-01 00:00:00+00', INTERVAL 90 DAY))
),
DayOneBehavior AS (
  SELECT e.user_pseudo_id,
    COUNT(DISTINCT (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location')) AS unique_pages_viewed,
    COUNTIF(e.event_name = 'add_to_cart') AS cart_adds_day_1,
    MAX(e.event_timestamp) - MIN(e.event_timestamp) AS day_1_duration_micros
  FROM events e
  INNER JOIN FirstVisit f ON e.user_pseudo_id = f.user_pseudo_id
  WHERE e.event_timestamp <= f.first_seen_time + 86400000000
  GROUP BY 1
),
FutureLTV AS (
  SELECT e.user_pseudo_id, SUM(e.ecommerce.purchase_revenue) AS actual_90d_ltv
  FROM events e JOIN FirstVisit f USING (user_pseudo_id)
  WHERE e.event_name = 'purchase'
    AND e.event_timestamp < f.first_seen_time + 7776000000000  -- 90 days in microseconds
  GROUP BY 1
),
Training AS (
  SELECT b.user_pseudo_id, b.unique_pages_viewed, b.cart_adds_day_1, b.day_1_duration_micros,
         COALESCE(l.actual_90d_ltv, 0) AS target_ltv
  FROM DayOneBehavior b LEFT JOIN FutureLTV l ON b.user_pseudo_id = l.user_pseudo_id
)
SELECT COUNT(*) AS users, COUNTIF(target_ltv = 0) AS zero_label_users,
       ROUND(AVG(target_ltv), 4) AS mean_label, ROUND(SUM(target_ltv), 2) AS sum_label
FROM Training;
