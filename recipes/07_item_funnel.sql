-- 07. Item-level cart abandonment, double UNNEST (public dataset)
-- Data: bigquery-public-data.ga4_obfuscated_sample_ecommerce, all 92 days (2020-11-01..2021-01-31).
-- Naive traps: the join on (user, item) is many-to-many with no aggregation first:
-- 3 adds x 2 purchases = 6 rows, so every count is inflated and the abandonment rate is pushed
-- toward 0 (it cannot go negative, see the fanout query); it links purchases made BEFORE the
-- add; no _TABLE_SUFFIX (full history scan); it counts events, not users.
-- Fix: one row per (user, item) on each side, then count users who added, and users who
-- bought at or after their first add.
-- Output is summarised (item count, negative rates, min/max) to keep the frozen result small.

-- @query naive
WITH AddToCarts AS (
  SELECT user_pseudo_id, item.item_id, item.item_name
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, UNNEST(items) AS item
  WHERE event_name = 'add_to_cart'
),
Purchases AS (
  SELECT user_pseudo_id, item.item_id
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, UNNEST(items) AS item
  WHERE event_name = 'purchase'
),
Funnel AS (
  SELECT a.item_id, a.item_name,
         COUNT(a.item_id) AS total_adds_to_cart,
         COUNT(p.item_id) AS total_purchases,
         ROUND(1 - (COUNT(p.item_id) / COUNT(a.item_id)), 4) AS abandonment_rate
  FROM AddToCarts a
  LEFT JOIN Purchases p ON a.user_pseudo_id = p.user_pseudo_id AND a.item_id = p.item_id
  GROUP BY 1, 2
  HAVING total_adds_to_cart > 100
)
SELECT COUNT(*) AS items, COUNTIF(abandonment_rate < 0) AS negative_rates,
       MIN(abandonment_rate) AS min_rate, MAX(abandonment_rate) AS max_rate,
       SUM(total_adds_to_cart) AS sum_adds, SUM(total_purchases) AS sum_purchases
FROM Funnel;

-- @query fanout
-- How much the naive (user, item) join multiplies rows, and why its rate can never go
-- negative: in a LEFT JOIN every row has a.item_id, so COUNT(p.item_id) <= COUNT(a.item_id).
WITH AddToCarts AS (
  SELECT user_pseudo_id, item.item_id
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, UNNEST(items) AS item
  WHERE event_name = 'add_to_cart'
),
Purchases AS (
  SELECT user_pseudo_id, item.item_id
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, UNNEST(items) AS item
  WHERE event_name = 'purchase'
),
Joined AS (
  SELECT a.item_id, a.user_pseudo_id, p.item_id AS p_item_id
  FROM AddToCarts a
  LEFT JOIN Purchases p ON a.user_pseudo_id = p.user_pseudo_id AND a.item_id = p.item_id
)
SELECT
  (SELECT COUNT(*) FROM AddToCarts) AS add_rows,
  (SELECT COUNT(*) FROM Purchases) AS purchase_rows,
  COUNT(*) AS joined_rows,
  COUNT(p_item_id) AS joined_rows_with_purchase,
  (SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM Joined GROUP BY item_id, user_pseudo_id)) AS max_rows_one_user_item
FROM Joined;

-- @query fixed
WITH AddToCarts AS (
  SELECT user_pseudo_id, item.item_id, ANY_VALUE(item.item_name) AS item_name,
         MIN(event_timestamp) AS first_add_ts
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, UNNEST(items) AS item
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131' AND event_name = 'add_to_cart'
  GROUP BY 1, 2
),
Purchases AS (
  SELECT user_pseudo_id, item.item_id, MAX(event_timestamp) AS last_purchase_ts
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, UNNEST(items) AS item
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131' AND event_name = 'purchase'
  GROUP BY 1, 2
),
Funnel AS (
  SELECT a.item_id, ANY_VALUE(a.item_name) AS item_name,
         COUNT(*) AS users_adding,
         COUNTIF(p.last_purchase_ts >= a.first_add_ts) AS users_adding_then_buying,
         ROUND(1 - SAFE_DIVIDE(COUNTIF(p.last_purchase_ts >= a.first_add_ts), COUNT(*)), 4) AS abandonment_rate
  FROM AddToCarts a
  LEFT JOIN Purchases p USING (user_pseudo_id, item_id)
  GROUP BY a.item_id
  HAVING users_adding > 100
)
SELECT COUNT(*) AS items, COUNTIF(abandonment_rate < 0) AS negative_rates,
       MIN(abandonment_rate) AS min_rate, MAX(abandonment_rate) AS max_rate,
       SUM(users_adding) AS sum_adds, SUM(users_adding_then_buying) AS sum_purchases
FROM Funnel;
