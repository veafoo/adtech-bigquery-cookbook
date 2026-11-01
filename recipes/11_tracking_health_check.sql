-- 11. Tracking health check: day vs 7-day moving average
-- Run date fixed at 2021-01-31 instead of CURRENT_DATE().
-- Truth: 10 orders a day from 01-01 to 01-30, except 01-27 when tracking was down (0 orders).
-- 01-30 has 2 duplicate re-fires (12 rows, 10 orders). 01-31 is today and still partial (2).
-- Expected: a CRITICAL alert on 01-27, nothing else.
-- Naive traps: a day with zero conversions has no row, so the outage never alerts and the
-- 7-row window spans more than 7 days; today is included and alerts every morning;
-- COUNT counts duplicates.

-- @fixture
sales AS (
  SELECT CONCAT(FORMAT_DATE('%Y%m%d', d), '-', CAST(n AS STRING)) AS transaction_id,
         TIMESTAMP_ADD(TIMESTAMP(d), INTERVAL n HOUR) AS event_timestamp
  FROM UNNEST(GENERATE_DATE_ARRAY('2021-01-01', '2021-01-30')) AS d, UNNEST(GENERATE_ARRAY(1, 10)) AS n
  WHERE d != '2021-01-27'
  UNION ALL SELECT '20210130-1', TIMESTAMP '2021-01-30 20:00:00+00'  -- duplicate re-fires
  UNION ALL SELECT '20210130-2', TIMESTAMP '2021-01-30 21:00:00+00'
  UNION ALL SELECT CONCAT('20210131-', CAST(n AS STRING)), TIMESTAMP_ADD(TIMESTAMP '2021-01-31', INTERVAL n HOUR)
            FROM UNNEST([1, 2]) AS n
)

-- @query naive
WITH DailyConversions AS (
  SELECT DATE(event_timestamp) AS event_date, COUNT(transaction_id) AS total_conversions
  FROM sales
  GROUP BY event_date
),
MovingAverages AS (
  SELECT event_date, total_conversions,
    AVG(total_conversions) OVER (ORDER BY event_date ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING) AS moving_avg_7d
  FROM DailyConversions
)
SELECT event_date, total_conversions, ROUND(moving_avg_7d, 2) AS expected_conversions,
  CASE WHEN total_conversions < (0.5 * moving_avg_7d) THEN 'CRITICAL ALERT: 50% DROP'
       WHEN total_conversions < (0.8 * moving_avg_7d) THEN 'WARNING: 20% DROP'
       ELSE 'HEALTHY' END AS tracking_status
FROM MovingAverages
ORDER BY event_date DESC LIMIT 14;

-- @query fixed
WITH Daily AS (
  SELECT DATE(event_timestamp) AS d, COUNT(DISTINCT transaction_id) AS n
  FROM sales
  WHERE DATE(event_timestamp) BETWEEN DATE_SUB(DATE '2021-01-31', INTERVAL 30 DAY)
                                  AND DATE_SUB(DATE '2021-01-31', INTERVAL 1 DAY)  -- full days only
  GROUP BY d
),
Filled AS (  -- date spine: a day with no data still gets a row, with 0
  SELECT d AS event_date, COALESCE(n, 0) AS total_conversions
  FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(DATE '2021-01-31', INTERVAL 30 DAY),
                                  DATE_SUB(DATE '2021-01-31', INTERVAL 1 DAY))) AS d
  LEFT JOIN Daily USING (d)
),
MovingAverages AS (
  SELECT *, AVG(total_conversions) OVER (ORDER BY event_date ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING) AS moving_avg_7d
  FROM Filled
)
SELECT event_date, total_conversions, ROUND(moving_avg_7d, 2) AS expected_conversions,
  CASE WHEN total_conversions < 0.5 * moving_avg_7d THEN 'CRITICAL ALERT: 50% DROP'
       WHEN total_conversions < 0.8 * moving_avg_7d THEN 'WARNING: 20% DROP'
       ELSE 'HEALTHY' END AS tracking_status
FROM MovingAverages
ORDER BY event_date DESC LIMIT 14;
