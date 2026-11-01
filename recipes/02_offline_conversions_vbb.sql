-- 02. Offline conversion upload for value-based bidding (CRM leads x sales x margin)
-- "Now" fixed at 2021-01-31 12:00 UTC instead of CURRENT_TIMESTAMP().
-- Truth: client A has two consented clicks (gclid g1 on 01-29, wbraid w1 on 01-30) and two
-- closed sales in the last 24 h. S1 sells product P2 (revenue 200, cost 120) -> margin 80,
-- credited to the last click before the sale (w1, a wbraid). S2 sells P3, absent from the
-- catalogue: no margin, so it must not be uploaded. Client B denied consent.
-- Expected upload: exactly one row (wbraid w1, order S1, value 80, time with timezone).
-- Naive traps: lead x sale fan-out; cost taken from the LEAD's product; every click ID sent
-- as wbraid; a raw timestamp where the Google Ads API wants "yyyy-mm-dd hh:mm:ss+hh:mm";
-- NULL margins uploaded; no order_id to dedup re-runs.

-- @fixture
leads AS (
  SELECT * FROM UNNEST([
    STRUCT('A' AS client_id, 'g1' AS click_id, 'gclid' AS click_id_type, 'P1' AS product_id,
           'GRANTED' AS consent_status, 'GRANTED' AS ad_user_data_consent,
           TIMESTAMP '2021-01-29 10:00:00+00' AS click_time),
    ('A', 'w1', 'wbraid', 'P2', 'GRANTED', 'GRANTED', TIMESTAMP '2021-01-30 09:00:00+00'),
    ('B', 'b1', 'gbraid', 'P1', 'DENIED',  'DENIED',  TIMESTAMP '2021-01-30 10:00:00+00')
  ])
),
sales AS (
  SELECT *, sale_ts AS sale_date FROM UNNEST([
    STRUCT('S1' AS sale_id, 'A' AS client_id, 'P2' AS product_id, 'CLOSED_WON' AS status,
           TIMESTAMP '2021-01-31 08:00:00+00' AS sale_ts, 200.0 AS gross_revenue),
    ('S2', 'A', 'P3', 'CLOSED_WON', TIMESTAMP '2021-01-31 09:00:00+00', 80.0),
    ('S3', 'B', 'P1', 'CLOSED_WON', TIMESTAMP '2021-01-31 10:00:00+00', 100.0),
    ('S4', 'A', 'P1', 'LOST',       TIMESTAMP '2021-01-31 11:00:00+00', 500.0)
  ])
),
catalogue AS (
  SELECT * FROM UNNEST([STRUCT('P1' AS product_id, 40.0 AS cogs_cost), ('P2', 120.0)])
)

-- @query naive
WITH ConsentedLeads AS (
  SELECT client_id, click_id, product_id FROM leads WHERE consent_status = 'GRANTED'
),
ClosedSales AS (
  SELECT client_id, sale_date, gross_revenue FROM sales WHERE status = 'CLOSED_WON'
),
ProductCosts AS (
  SELECT product_id, cogs_cost FROM catalogue
)
SELECT
  l.click_id AS wbraid,
  s.sale_date AS conversion_date_time,
  (s.gross_revenue - p.cogs_cost) AS conversion_value,
  'EUR' AS currency_code,
  'GRANTED' AS ad_user_data
FROM ConsentedLeads l
INNER JOIN ClosedSales s ON l.client_id = s.client_id
LEFT JOIN ProductCosts p ON l.product_id = p.product_id
WHERE s.sale_date >= TIMESTAMP_SUB(TIMESTAMP '2021-01-31 12:00:00+00', INTERVAL 24 HOUR)
ORDER BY conversion_date_time, wbraid;  -- ORDER BY added only to make the output stable

-- @query fixed
WITH ClosedSales AS (
  SELECT sale_id, client_id, product_id, sale_ts, gross_revenue
  FROM sales
  WHERE status = 'CLOSED_WON'
    AND sale_ts >= TIMESTAMP_SUB(TIMESTAMP '2021-01-31 12:00:00+00', INTERVAL 24 HOUR)
),
LastClick AS (  -- one click per sale: the last consented click before it
  SELECT s.*, l.click_id, l.click_id_type
  FROM ClosedSales s
  JOIN leads l ON l.client_id = s.client_id AND l.click_time < s.sale_ts
  WHERE l.ad_user_data_consent = 'GRANTED'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY s.sale_id ORDER BY l.click_time DESC) = 1
)
SELECT
  IF(c.click_id_type = 'gclid',  c.click_id, NULL) AS gclid,
  IF(c.click_id_type = 'gbraid', c.click_id, NULL) AS gbraid,
  IF(c.click_id_type = 'wbraid', c.click_id, NULL) AS wbraid,
  c.sale_id AS order_id,  -- dedup key if the batch re-runs
  FORMAT_TIMESTAMP('%F %T%Ez', c.sale_ts, 'Europe/Paris') AS conversion_date_time,
  c.gross_revenue - p.cogs_cost AS conversion_value,  -- cost of the product SOLD
  'EUR' AS currency_code,
  'GRANTED' AS ad_user_data
FROM LastClick c
JOIN catalogue p ON p.product_id = c.product_id  -- inner join: no NULL margin uploaded
WHERE p.cogs_cost IS NOT NULL
ORDER BY order_id;
