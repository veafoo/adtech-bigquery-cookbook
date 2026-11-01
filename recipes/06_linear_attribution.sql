-- 06. Linear multi-touch attribution
-- Truth: client A converts twice: X1 (100) on 01-20 and X2 (200) on 01-30, total 300.
-- Clicks: C0 (2020-06-01, outside a 90-day lookback), C1 (01-10), a click with no campaign
-- (01-12), C2 (01-15), C3 (01-25, after X1, before X2).
-- Linear credit within 90 days: X1 -> C1, C2 (50 each); X2 -> C1, C2, C3 (66.67 each).
-- Expected: C1 116.67, C2 116.67, C3 66.67, total 300.
-- Naive traps: the touch count is taken after the join with ALL conversions (T x N rows), so
-- each client is credited its average order value, not its total; NULL campaigns are not
-- counted but still receive credit; no lookback and post-conversion clicks get credit.

-- @fixture
ad_clicks AS (
  SELECT * FROM UNNEST([
    STRUCT('A' AS client_id, 'C0' AS campaign_name, TIMESTAMP '2020-06-01 00:00:00+00' AS click_time),
    ('A', 'C1', TIMESTAMP '2021-01-10 00:00:00+00'),
    ('A', NULL, TIMESTAMP '2021-01-12 00:00:00+00'),
    ('A', 'C2', TIMESTAMP '2021-01-15 00:00:00+00'),
    ('A', 'C3', TIMESTAMP '2021-01-25 00:00:00+00')
  ])
),
sales AS (
  SELECT * FROM UNNEST([
    STRUCT('A' AS client_id, 'X1' AS conversion_id, 100.0 AS conversion_value, TIMESTAMP '2021-01-20 00:00:00+00' AS conversion_time),
    ('A', 'X2', 200.0, TIMESTAMP '2021-01-30 00:00:00+00')
  ])
)

-- @query naive
WITH Touchpoints AS (
  SELECT client_id, campaign_name FROM ad_clicks
),
Conversions AS (
  SELECT client_id, conversion_value FROM sales
),
AttributionBase AS (
  SELECT t.client_id, t.campaign_name, c.conversion_value,
         COUNT(t.campaign_name) OVER (PARTITION BY t.client_id) AS total_touchpoints
  FROM Touchpoints t
  INNER JOIN Conversions c ON t.client_id = c.client_id
)
SELECT campaign_name, SUM(conversion_value / total_touchpoints) AS linear_attributed_revenue
FROM AttributionBase
GROUP BY campaign_name
ORDER BY linear_attributed_revenue DESC, campaign_name;  -- tie-break added for a stable output

-- @query fixed
WITH AttributionBase AS (
  SELECT c.conversion_id, t.campaign_name, c.conversion_value,
         COUNT(*) OVER (PARTITION BY c.conversion_id) AS total_touchpoints
  FROM sales c
  JOIN ad_clicks t
    ON t.client_id = c.client_id
   AND t.click_time <= c.conversion_time
   AND t.click_time >= TIMESTAMP_SUB(c.conversion_time, INTERVAL 90 DAY)
  WHERE t.campaign_name IS NOT NULL
)
SELECT campaign_name, ROUND(SUM(conversion_value / total_touchpoints), 2) AS linear_attributed_revenue
FROM AttributionBase
GROUP BY campaign_name
ORDER BY linear_attributed_revenue DESC, campaign_name;
