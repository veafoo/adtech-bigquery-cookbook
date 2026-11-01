-- 03. Clean-room style impression x conversion join (Ads Data Hub logic, run on a fixture)
-- This does NOT run ADH. Outside ADH there is no adh.* table; the fixture only reproduces
-- the join logic. In ADH, impression time is TIMESTAMP_MICROS(query_id.time_usec), user_id
-- is opaque and joins to first-party data only through UPDM, and ADH applies its own
-- privacy thresholds (the analyst's HAVING is redundant there).
-- Truth: C1 influenced u1 (100) and u2 (50) = 150. C2 influenced u3 (70) = 70;
-- u4 saw C2 only after converting.
-- Naive traps: SUM over impression x conversion rows counts u1's 100 three times (3 impressions);
-- the HAVING threshold is scaled from 50 to 2 for this 4-user fixture and silently drops C2.

-- @fixture
impressions AS (
  SELECT * FROM UNNEST([
    STRUCT('C1' AS campaign_id, 'u1' AS user_id, TIMESTAMP '2021-01-10 00:00:00+00' AS impression_time),
    ('C1', 'u1', TIMESTAMP '2021-01-11 00:00:00+00'),
    ('C1', 'u1', TIMESTAMP '2021-01-12 00:00:00+00'),
    ('C1', 'u2', TIMESTAMP '2021-01-15 00:00:00+00'),
    ('C2', 'u3', TIMESTAMP '2021-01-10 00:00:00+00'),
    ('C2', 'u3', TIMESTAMP '2021-01-11 00:00:00+00'),
    ('C2', 'u4', TIMESTAMP '2021-01-25 00:00:00+00')
  ])
),
conversions AS (
  SELECT * FROM UNNEST([
    STRUCT('u1' AS user_id, 'X1' AS conversion_id, TIMESTAMP '2021-01-20 00:00:00+00' AS conversion_time, 100.0 AS conversion_value),
    ('u2', 'X2', TIMESTAMP '2021-01-20 00:00:00+00', 50.0),
    ('u3', 'X3', TIMESTAMP '2021-01-20 00:00:00+00', 70.0),
    ('u4', 'X4', TIMESTAMP '2021-01-20 00:00:00+00', 30.0)
  ])
)

-- @query naive
WITH MatchedConversions AS (
  SELECT imp.campaign_id, imp.user_id AS adh_user_id, conv.conversion_value
  FROM impressions imp
  INNER JOIN conversions conv ON imp.user_id = conv.user_id
  WHERE imp.impression_time < conv.conversion_time
)
SELECT campaign_id,
       COUNT(DISTINCT adh_user_id) AS unique_converters,
       SUM(conversion_value) AS total_revenue
FROM MatchedConversions
GROUP BY campaign_id
HAVING COUNT(DISTINCT adh_user_id) >= 2  -- 50 in the original, scaled to the fixture
ORDER BY total_revenue DESC;

-- @query fixed
WITH ImpConv AS (
  SELECT DISTINCT imp.campaign_id, imp.user_id, conv.conversion_id, conv.conversion_value
  FROM impressions imp
  JOIN conversions conv ON imp.user_id = conv.user_id
  WHERE imp.impression_time < conv.conversion_time
)
SELECT campaign_id,
       COUNT(DISTINCT user_id) AS unique_converters,
       SUM(conversion_value)   AS influenced_revenue  -- one count per conversion per campaign
FROM ImpConv
GROUP BY campaign_id
ORDER BY influenced_revenue DESC;
