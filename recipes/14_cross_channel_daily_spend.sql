-- 14. One daily cross-channel table: date x channel x spend_eur, impressions, clicks
-- Runs on the synthetic data in synth/ (seed 42, 28 days from 2026-10-01): load it with
-- synth/load.sh and set COOKBOOK_SYNTH_DATASET=<project>.<dataset>. Skipped without it.
-- Six platforms, six native shapes (see README, "Layer 2"):
--   Google Ads  cost in micros (EUR, account currency), UTC day
--   DV360       one row per impression, money in nanos in 3 currency variants, UTC day
--   Meta        every number is a string; one day sends spend "" for a true 0; Paris day
--   TikTok      every number is a string; stat_time_day is 'yyyy-MM-dd HH:mm:ss'; Paris day
--   Amazon DSP  v3 names, totalCost is a decimal in orderCurrency (EUR); UTC day
--   TTD         impressions cost in USD (convert with AdvertiserCurrencyExchangeRateFromUSD);
--               clicks are log lines, ~2 % of them reprocessed twice; LogEntryTime is UTC text
-- Day convention used by the fixed query: each platform's day is kept as it reports it.
-- Meta and TikTok are Europe/Paris days, the rest UTC days. Day-level rows cannot be shifted
-- to another timezone, so a Paris day and a UTC day on the same date overlap by 1-2 hours;
-- that is stated, not corrected. Spend is EUR: Google micros / 1e6, DV360 Advertiser Currency
-- nanos / 1e9, TTD USD cost x its exchange rate.
-- Truth (recomputed in Python from the generated files, over the 28 days; spend is the sum of
-- the daily rows, each rounded to cents like the table):
-- TRUTH google_ads spend_eur=4936.00 impressions=223067 clicks=8410
-- TRUTH dv360 spend_eur=175.35 impressions=34702 clicks=null
-- TRUTH meta spend_eur=2125.52 impressions=320696 clicks=5433
-- TRUTH tiktok spend_eur=1969.40 impressions=487116 clicks=5874
-- TRUTH amazon_dsp spend_eur=1255.25 impressions=270282 clicks=1577
-- TRUTH ttd spend_eur=166.12 impressions=32885 clicks=998
-- TRUTH total spend_eur=10627.64 impressions=1368748 clicks=22292
-- TRUTH meta_empty_day date=2026-10-27 spend_eur=0.00 impressions=0 clicks=0
-- Naive traps: raw cost columns are summed as if they were EUR (micros, nanos and USD mixed
-- with decimals); SAFE_CAST of TikTok's datetime string to DATE gives NULL (a plain CAST fails with
-- "Invalid date"), so its rows land on a NULL
-- day; Meta's empty spend becomes NULL instead of 0 on 2026-10-27 (a plain CAST('' AS FLOAT64)
-- fails the whole query with "Bad double value"); TTD clicks are counted with their
-- duplicates; the generator writes only the DV360 impression file, so its clicks stay NULL,
-- never 0.

-- @query naive
SELECT date, channel,
       ROUND(SUM(spend), 2) AS spend_eur,
       SUM(impressions) AS impressions,
       SUM(clicks) AS clicks
FROM (
  SELECT segments_date AS date, 'google_ads' AS channel,
         CAST(metrics_cost_micros AS FLOAT64) AS spend,
         metrics_impressions AS impressions, metrics_clicks AS clicks
  FROM `@@SYNTH@@.google_ads_campaign_stats`
  UNION ALL
  SELECT DATE(TIMESTAMP_MICROS(dv360_request_time)), 'dv360',
         CAST(dv360_media_cost_usd AS FLOAT64), 1, NULL
  FROM `@@SYNTH@@.dv360_impressions`
  UNION ALL
  SELECT SAFE_CAST(date_start AS DATE), 'meta',
         SAFE_CAST(spend AS FLOAT64), SAFE_CAST(impressions AS INT64), SAFE_CAST(clicks AS INT64)
  FROM `@@SYNTH@@.meta_insights`
  UNION ALL
  SELECT SAFE_CAST(stat_time_day AS DATE), 'tiktok',
         SAFE_CAST(spend AS FLOAT64), SAFE_CAST(impressions AS INT64), SAFE_CAST(clicks AS INT64)
  FROM `@@SYNTH@@.tiktok_integrated_report`
  UNION ALL
  SELECT SAFE_CAST(date AS DATE), 'amazon_dsp',
         CAST(totalCost AS FLOAT64), impressions, clickThroughs
  FROM `@@SYNTH@@.amazon_dsp_campaign`
  UNION ALL
  SELECT DATE(TIMESTAMP(LogEntryTime)), 'ttd',
         AdvertiserCostInUSDollars, 1, NULL
  FROM `@@SYNTH@@.ttd_impressions`
  UNION ALL
  SELECT DATE(TIMESTAMP(LogEntryTime)), 'ttd', 0, 0, 1
  FROM `@@SYNTH@@.ttd_clicks`
)
GROUP BY date, channel
ORDER BY date, channel;  -- ORDER BY added only to make the output stable

-- @query fixed
WITH deduped_clicks AS (
  -- a click is one ClickRedirectId; keep the earliest ProcessedTime
  SELECT * EXCEPT (rn)
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY ClickRedirectId
                                 ORDER BY SAFE.PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S', ProcessedTime)) AS rn
    FROM `@@SYNTH@@.ttd_clicks`
  )
  WHERE rn = 1
),
unified AS (
  -- Google Ads: micros -> EUR
  SELECT segments_date AS date, 'google_ads' AS channel,
         CAST(metrics_cost_micros AS NUMERIC) / 1000000 AS spend_eur,
         metrics_impressions AS impressions, metrics_clicks AS clicks
  FROM `@@SYNTH@@.google_ads_campaign_stats`
  UNION ALL
  -- DV360: nanos in the Advertiser Currency variant (EUR) -> EUR, UTC day of the request
  SELECT DATE(TIMESTAMP_MICROS(dv360_request_time)), 'dv360',
         CAST(dv360_media_cost_advertiser_currency AS NUMERIC) / 1000000000, 1, NULL
  FROM `@@SYNTH@@.dv360_impressions`
  UNION ALL
  -- Meta: strings; "" means no delivery, so spend, impressions and clicks are 0, not NULL.
  -- The day stays the Paris day the API reports.
  SELECT SAFE_CAST(date_start AS DATE), 'meta',
         COALESCE(SAFE_CAST(spend AS NUMERIC), 0),
         COALESCE(SAFE_CAST(impressions AS INT64), 0), COALESCE(SAFE_CAST(clicks AS INT64), 0)
  FROM `@@SYNTH@@.meta_insights`
  UNION ALL
  -- TikTok: strings; the day is the first 10 characters of stat_time_day (Paris day)
  SELECT SAFE_CAST(SUBSTR(stat_time_day, 1, 10) AS DATE), 'tiktok',
         COALESCE(SAFE_CAST(spend AS NUMERIC), 0),
         COALESCE(SAFE_CAST(impressions AS INT64), 0), COALESCE(SAFE_CAST(clicks AS INT64), 0)
  FROM `@@SYNTH@@.tiktok_integrated_report`
  UNION ALL
  -- Amazon DSP v3: totalCost is already a decimal in orderCurrency (EUR)
  SELECT SAFE_CAST(date AS DATE), 'amazon_dsp', totalCost, impressions, clickThroughs
  FROM `@@SYNTH@@.amazon_dsp_campaign`
  UNION ALL
  -- TTD impressions: USD cost x USD->EUR rate, UTC day parsed from the 'yyyy-MM-ddTHH:mm:ss' text
  SELECT DATE(SAFE.PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S', LogEntryTime)), 'ttd',
         AdvertiserCostInUSDollars * AdvertiserCurrencyExchangeRateFromUSD, 1, NULL
  FROM `@@SYNTH@@.ttd_impressions`
  UNION ALL
  -- TTD clicks, deduplicated
  SELECT DATE(SAFE.PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S', LogEntryTime)), 'ttd', 0, 0, 1
  FROM deduped_clicks
)
SELECT date, channel,
       ROUND(SUM(spend_eur), 2) AS spend_eur,
       SUM(impressions) AS impressions,
       SUM(clicks) AS clicks
FROM unified
GROUP BY date, channel
ORDER BY date, channel;
