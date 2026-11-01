-- 01. Purchase dedup with ROW_NUMBER
-- Run date fixed at 2021-01-31 (UTC) instead of CURRENT_DATE() so results are stable.
-- Truth for 2021-01-31: T2 (50) + three purchases without an ID (30 + 40 + 20) = 140.
-- T1 fired at 23:58 on the 30th and again at 00:01 on the 31st (refresh after midnight):
-- it belongs to the 30th and must not be counted again on the 31st.
-- Naive traps: NULL transaction_id rows share one partition and collapse into one row;
-- the today-only filter hides yesterday's first occurrence; no tie-breaker.

-- @fixture
raw_purchase_events AS (
  SELECT * FROM UNNEST([
    STRUCT('T1' AS transaction_id, 'c1' AS click_id, TIMESTAMP '2021-01-30 23:58:00+00' AS event_timestamp, 100.0 AS conversion_value),
    ('T1', 'c1', TIMESTAMP '2021-01-31 00:01:00+00', 100.0),
    ('T2', 'c2', TIMESTAMP '2021-01-31 10:00:00+00', 50.0),
    (NULL, 'c4', TIMESTAMP '2021-01-31 11:00:00+00', 30.0),
    (NULL, 'c5', TIMESTAMP '2021-01-31 12:00:00+00', 40.0),
    ('',   'c6', TIMESTAMP '2021-01-31 13:00:00+00', 20.0)
  ])
)

-- @query naive
WITH RankedConversions AS (
  SELECT transaction_id, click_id, event_timestamp, conversion_value,
         ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY event_timestamp ASC) AS dedup_rank
  FROM raw_purchase_events
  WHERE DATE(event_timestamp) = DATE '2021-01-31'
)
SELECT transaction_id, click_id, event_timestamp, conversion_value
FROM RankedConversions
WHERE dedup_rank = 1
ORDER BY event_timestamp;  -- ORDER BY added only to make the output stable

-- @query fixed
WITH RankedConversions AS (
  SELECT transaction_id, click_id, event_timestamp, conversion_value,
         ROW_NUMBER() OVER (PARTITION BY transaction_id
                            ORDER BY event_timestamp ASC, click_id) AS dedup_rank
  FROM raw_purchase_events
  WHERE DATE(event_timestamp) >= DATE_SUB(DATE '2021-01-31', INTERVAL 1 DAY)  -- look back over midnight
    AND transaction_id IS NOT NULL AND transaction_id != ''
)
SELECT transaction_id, click_id, event_timestamp, conversion_value
FROM RankedConversions
WHERE dedup_rank = 1 AND DATE(event_timestamp) = DATE '2021-01-31'
UNION ALL
-- rows without an ID are kept (and can be flagged), never collapsed into one
SELECT transaction_id, click_id, event_timestamp, conversion_value
FROM raw_purchase_events
WHERE DATE(event_timestamp) = DATE '2021-01-31' AND (transaction_id IS NULL OR transaction_id = '')
ORDER BY event_timestamp;
