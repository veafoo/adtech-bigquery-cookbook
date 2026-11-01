-- 12. Monthly unique users with HyperLogLog++ sketches (public dataset)
-- Data: bigquery-public-data.ga4_obfuscated_sample_ecommerce, all 92 days.
-- The HLL query is correct (daily HLL_COUNT.INIT, monthly HLL_COUNT.MERGE).
-- Claims tested: "about 1% error" (default precision 15: about 1.15% at 95% per the BigQuery
-- sketches page) and "cost divided by 100". Both queries read the same user_pseudo_id column:
-- compare the dry-run bytes the runner records. The saving only comes when the daily sketches
-- are materialised in a table and re-merged without reading raw events again.

-- @query hll
WITH DailySketches AS (
  SELECT PARSE_DATE('%Y%m%d', event_date) AS d, HLL_COUNT.INIT(user_pseudo_id) AS daily_users_sketch
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  GROUP BY d
)
SELECT EXTRACT(YEAR FROM d) AS year, EXTRACT(MONTH FROM d) AS month,
       HLL_COUNT.MERGE(daily_users_sketch) AS approx_unique_users
FROM DailySketches
GROUP BY 1, 2
ORDER BY 1 DESC, 2 DESC;

-- @query exact
SELECT EXTRACT(YEAR FROM PARSE_DATE('%Y%m%d', event_date)) AS year,
       EXTRACT(MONTH FROM PARSE_DATE('%Y%m%d', event_date)) AS month,
       COUNT(DISTINCT user_pseudo_id) AS exact_unique_users
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
GROUP BY 1, 2
ORDER BY 1 DESC, 2 DESC;
