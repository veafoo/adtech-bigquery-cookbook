-- 15. TTD click dedup, then CTR per campaign
-- Runs on the synthetic data in synth/ (seed 42): see recipe 14 for the setup. Skipped without it.
-- REDS reprocesses some click log lines: the same ClickRedirectId comes back with a later
-- ProcessedTime. About 2 % of the rows here (20 of 1018). A click is one ClickRedirectId.
-- The fixed query keeps the earliest ProcessedTime per ClickRedirectId, then divides by the
-- impressions of the same CampaignId.
-- Truth (recomputed in Python from the generated files):
-- TRUTH ttdcmp1 impressions=10952 clicks_raw=220 clicks_dedup=219 ctr_raw=0.0201 ctr_dedup=0.0200
-- TRUTH ttdcmp2 impressions=10882 clicks_raw=343 clicks_dedup=332 ctr_raw=0.0315 ctr_dedup=0.0305
-- TRUTH ttdcmp3 impressions=11051 clicks_raw=455 clicks_dedup=447 ctr_raw=0.0412 ctr_dedup=0.0404
-- TRUTH duplicate_rows rows=20
-- Naive trap: COUNT(*) of click rows against impressions overstates every CTR by the share of
-- duplicates in that campaign (0.5 % to 3.3 % relative).

-- @query naive
SELECT i.CampaignId AS campaign_id,
       i.impressions,
       c.clicks,
       ROUND(SAFE_DIVIDE(c.clicks, i.impressions), 4) AS ctr
FROM (SELECT CampaignId, COUNT(*) AS impressions
      FROM `@@SYNTH@@.ttd_impressions` GROUP BY CampaignId) AS i
JOIN (SELECT CampaignId, COUNT(*) AS clicks
      FROM `@@SYNTH@@.ttd_clicks` GROUP BY CampaignId) AS c
  USING (CampaignId)
ORDER BY campaign_id;  -- ORDER BY added only to make the output stable

-- @query fixed
WITH deduped_clicks AS (
  SELECT * EXCEPT (rn)
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY ClickRedirectId
                                 ORDER BY SAFE.PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S', ProcessedTime)) AS rn
    FROM `@@SYNTH@@.ttd_clicks`
  )
  WHERE rn = 1
)
SELECT i.CampaignId AS campaign_id,
       i.impressions,
       c.clicks,
       ROUND(SAFE_DIVIDE(c.clicks, i.impressions), 4) AS ctr
FROM (SELECT CampaignId, COUNT(DISTINCT ImpressionId) AS impressions
      FROM `@@SYNTH@@.ttd_impressions` GROUP BY CampaignId) AS i
JOIN (SELECT CampaignId, COUNT(*) AS clicks
      FROM deduped_clicks GROUP BY CampaignId) AS c
  USING (CampaignId)
ORDER BY campaign_id;

-- @query dup_check
-- proof: the click table holds more rows than distinct ClickRedirectIds
SELECT COUNT(*) AS click_rows,
       COUNT(DISTINCT ClickRedirectId) AS unique_clicks,
       COUNT(*) - COUNT(DISTINCT ClickRedirectId) AS duplicate_rows
FROM `@@SYNTH@@.ttd_clicks`;
