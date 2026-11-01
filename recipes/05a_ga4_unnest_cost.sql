-- 05a. GA4 export: does CROSS JOIN UNNEST "explode the bill"? (public dataset)
-- Data: bigquery-public-data.ga4_obfuscated_sample_ecommerce, purchases of 2021-01-28..31
-- (the 4 days up to a run date fixed at 2021-01-31).
-- Claim tested: "a CROSS JOIN UNNEST multiplies rows and explodes the bill".
-- On-demand billing counts bytes read from the referenced columns (down to nested leaf
-- fields), not output rows. The runner records the dry-run bytes of each query; both read
-- the same leaf fields, so compare their bytes with their output_rows.
-- CROSS JOIN multiplies rows (a correctness problem), not the bytes billed.
-- Also: this export has no 'gclid' key in event_params, so the naive gclid is always NULL.

-- @query scalar_subqueries
WITH q AS (
  SELECT
    event_date,
    TIMESTAMP_MICROS(event_timestamp) AS exact_time,
    user_pseudo_id,
    event_name,
    (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location') AS page_url,
    (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'gclid') AS gclid
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN FORMAT_DATE('%Y%m%d', DATE_SUB(DATE '2021-01-31', INTERVAL 3 DAY))
                          AND FORMAT_DATE('%Y%m%d', DATE '2021-01-31')
    AND event_name IN ('purchase', 'generate_lead')
)
SELECT COUNT(*) AS output_rows,
       COUNTIF(page_url IS NOT NULL) AS rows_with_page_url,
       COUNTIF(gclid IS NOT NULL) AS rows_with_gclid
FROM q;

-- @query cross_join_unnest
-- reads the same leaf fields (key, value.string_value) as the scalar version
SELECT COUNT(*) AS output_rows,
       COUNTIF(p.key = 'page_location' AND p.value.string_value IS NOT NULL) AS rows_with_page_url,
       COUNTIF(p.key = 'gclid' AND p.value.string_value IS NOT NULL) AS rows_with_gclid
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
CROSS JOIN UNNEST(event_params) AS p
WHERE _TABLE_SUFFIX BETWEEN FORMAT_DATE('%Y%m%d', DATE_SUB(DATE '2021-01-31', INTERVAL 3 DAY))
                        AND FORMAT_DATE('%Y%m%d', DATE '2021-01-31')
  AND event_name IN ('purchase', 'generate_lead');
