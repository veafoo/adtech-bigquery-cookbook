-- 04. Parsing raw server-side tagging hits stored as JSON
-- Fixture: one JSON object per hit with the flat GA4 request parameters (en, dl, gcs, gcd,
-- and custom event params as flat keys such as "ep.coupon"). The gcs/gcd values are
-- illustrative strings, not decoded here.
-- Truth: 2 purchases; the first landed with wbraid WB123 and carries coupon WINTER.
-- Naive traps: '$.ep.gcd' looks for a nested {"ep":{"gcd":..}} object that never exists,
-- gcd is a top-level parameter, and consent never travels as ep.ad_user_data: every row
-- comes out NULL / 'Missing Consent'. wbraid arrives in the landing URL (dl), not as a param.

-- @fixture
server_logs AS (
  SELECT * FROM UNNEST([
    STRUCT(TIMESTAMP '2021-01-31 10:00:00+00' AS timestamp,
           '{"en":"purchase","dl":"https://shop.example/checkout?wbraid=WB123&step=2","gcs":"G111","gcd":"13t3t3t3t5l1","ep.coupon":"WINTER"}' AS raw_payload),
    (TIMESTAMP '2021-01-31 11:00:00+00',
           '{"en":"purchase","dl":"https://shop.example/checkout","gcs":"G100","gcd":"13p3p2p2p5l1"}'),
    (TIMESTAMP '2021-01-31 12:00:00+00',
           '{"en":"page_view","dl":"https://shop.example/","gcs":"G111","gcd":"13t3t3t3t5l1"}')
  ])
)

-- @query naive
SELECT
  timestamp,
  JSON_EXTRACT_SCALAR(raw_payload, '$.ep.gcd') AS gcd_string,
  JSON_EXTRACT_SCALAR(raw_payload, '$.ep.wbraid') AS wbraid,
  CASE
    WHEN JSON_EXTRACT_SCALAR(raw_payload, '$.ep.ad_user_data') = 'GRANTED' THEN 'Valid DMA'
    WHEN JSON_EXTRACT_SCALAR(raw_payload, '$.ep.ad_user_data') = 'DENIED' THEN 'Blocked by DMA'
    ELSE 'Missing Consent'
  END AS dma_status
FROM server_logs
WHERE DATE(timestamp) = DATE '2021-01-31'
  AND JSON_EXTRACT_SCALAR(raw_payload, '$.en') = 'purchase'
ORDER BY timestamp;

-- @query fixed
SELECT
  timestamp,
  JSON_VALUE(raw_payload, '$.gcd') AS gcd_string,
  JSON_VALUE(raw_payload, '$.gcs') AS gcs_string,
  REGEXP_EXTRACT(JSON_VALUE(raw_payload, '$.dl'), r'[?&]wbraid=([^&#]+)') AS wbraid,
  JSON_VALUE(raw_payload, '$."ep.coupon"') AS coupon  -- a flat key with a dot must be quoted
FROM server_logs
WHERE DATE(timestamp) = DATE '2021-01-31'
  AND JSON_VALUE(raw_payload, '$.en') = 'purchase'
ORDER BY timestamp;
