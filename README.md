# adtech-bigquery-cookbook

**Author:** Radwan Mezzi

A query that runs is not a query that is right. In marketing analytics the expensive SQL bugs
rarely raise an error: the dashboard fills up, the number is wrong, and someone downstream
finds out later.

This repo collects BigQuery patterns that come up in marketing analytics: dedup, offline
conversion uploads, clean-room joins, the GA4 export, attribution, funnels, sessions, tracking
health checks, HyperLogLog, LTV labels. Each recipe puts the common version of a query, which
runs without error, next to a fixed one, and runs both:

- on a small fixture whose correct answer was worked out by hand before the first run, so the
  bug shows up as a wrong number in a test;
- or on Google's public GA4 sample, to measure how far the common version drifts on real data.

## How it works

- `recipes/*.sql`: one file per topic. Blocks: `-- @fixture` (optional CTEs), `-- @query naive`,
  `-- @query fixed`, and a proof query when a claim needs one.
- Fixtures are CTEs, so there is nothing to create (the free BigQuery sandbox accepts no DML).
  The public data is `bigquery-public-data.ga4_obfuscated_sample_ecommerce`, November 2020 to
  January 2021.
- `run_cookbook.py` sends each query with the `bq` CLI. It dry-runs first, refuses anything
  above 2 GB, and stores the bytes processed next to the rows. `--freeze` writes
  `expected/*.json`; without it, results are compared to those files.
- `test_cookbook.py`: unit tests for the runner, and one test per recipe pinning the number that
  shows the naive version is wrong.

## Run it

Needs the Google Cloud SDK (`gcloud`, `bq`) and Python 3.

```
gcloud auth login && gcloud config set project <your-project>
python3 -m unittest test_cookbook synth.test_generate -v   # offline: frozen results + generator tests
COOKBOOK_LIVE=1 python3 -m unittest test_cookbook    # re-runs everything on BigQuery, about 5.7 GB read
python3 run_cookbook.py --print 01 fixed             # the exact SQL sent, to paste in the console
```

The free BigQuery sandbox is enough.

## Recipes

| # | Topic | Common pattern | What the run shows |
|---|---|---|---|
| 01 | Purchase dedup | `ROW_NUMBER()` by `transaction_id`, today's rows only | revenue 200 instead of 140: a purchase re-fired after midnight is counted on a second day, and the two purchases with a NULL ID collapse into one row |
| 02 | Offline conversions for value-based bidding | lead x sale join, cost taken from the lead's product | 4 upload rows instead of 1, one of them with a negative value; every click ID sent as `wbraid`; no timezone offset on the conversion time |
| 03 | Clean-room impression x conversion join (logic only) | `SUM` over impression x conversion rows, plus a user threshold | 350 credited to a campaign that influenced 150; the threshold (50, scaled to 2 for the fixture) hides the second campaign |
| 04 | Server-side hits stored as JSON | `$.ep.gcd`, consent read from `ep.*` keys | every purchase comes out "Missing Consent"; the fix reads `gcd` and `gcs` at the top level of the JSON and `wbraid` from the landing URL |
| 05a | Does `CROSS JOIN UNNEST` cost more than scalar subqueries? | | same bytes processed (39,994,623) for 2,460 output rows vs 173: UNNEST changes the rows, not the cost |
| 05b | gclid on GA4 conversion events | gclid read from the purchase's `event_params` | NULL on every purchase; the fix carries `collected_traffic_source.gclid` over the session, keyed on user and session |
| 06 | Linear multi-touch attribution | touch count taken after joining every conversion | 187.5 credited instead of 300; credit goes to a NULL campaign and to a click older than 90 days |
| 07 | Cart abandonment per item | many-to-many join on (user, item) | up to 240 joined rows for one user and item; lowest abandonment rate 0.41 % vs 60 % once counted per user. The rate cannot go negative |
| 08 | 30-minute sessions with `LAG` | partition by `user_id` | anonymous hits all get a NULL session id |
| 08b | The same rule on GA4 data | | 2,824 custom sessions vs 2,849 GA4 sessions; `ga_session_id` alone gives 2,812 |
| 09 | Add-to-cart to purchase delay with `LEAD` | partition by `ga_session_id` only | 3,162 of 114,882 session ids are shared by several users; average 8.81 min vs 8.01 |
| 10 | Back-filling a missing id with `LAST_VALUE` | IP + user agent partition over a whole day, no `COALESCE` | visitor A's rows are overwritten with visitor B's id |
| 11 | Tracking health check on a moving average | average over the rows that exist | the outage day has no row and never alerts; today's partial day alerts every morning |
| 12 | Monthly unique users with HLL++ | | error between -0.006 % and +0.735 %, for the same bytes as an exact `COUNT(DISTINCT)` |
| 13 | 90-day LTV label | revenue of every purchase in the data, no 90-day window | 270,154 users labelled, 5,499 with 90 days actually observed; mean label 1.34 vs 2.43 |
| 14 | One daily cross-channel table (synthetic data) | raw cost columns summed, strings cast blindly | Google Ads micros and DV360 nanos added as if EUR (4,936,000,000 and 190,772,916,483 "euros" over 28 days, against 4,936.00 and 175.35); TikTok's `stat_time_day` through SAFE_CAST to DATE is NULL (a plain CAST fails), so all its rows land on a NULL day; Meta's empty spend is NULL on 2026-10-27, not 0; TTD clicks 1,018 instead of 998 |
| 15 | TTD click dedup and CTR (synthetic data) | `COUNT(*)` of click rows against impressions | 20 of 1,018 click rows are the same `ClickRedirectId` reprocessed; CTR per campaign overstated by 0.5 % to 3.3 % (campaign 2: 0.0315 against 0.0305) |

## Layer 2: synthetic cross-channel data

Recipes 14 and 15 do not use fixtures or the GA4 sample. They run on `synth/`, a seeded generator
for one fictional advertiser with 3 campaigns on each of six platforms, 28 days from 2026-10-01.
Each table keeps the field names and the native quirks of its platform's export, so the recipe
has to undo them. Nothing in it is real data.

```
python3 synth/generate.py --seed 42            # writes synth/*.ndjson and synth/MANIFEST.json
synth/load.sh <your-project>.adtech_synth      # creates the dataset (US), loads 7 tables with explicit schemas
COOKBOOK_SYNTH_DATASET=<your-project>.adtech_synth python3 run_cookbook.py --freeze --only 14,15
COOKBOOK_SYNTH_DATASET=<your-project>.adtech_synth python3 run_cookbook.py --only 14,15    # compare
```

Recipes name their tables `@@SYNTH@@.<table>`; the runner replaces the placeholder with
`COOKBOOK_SYNTH_DATASET`. Without that variable recipes 14 and 15 are skipped, not failed, and no
project ID appears in this repo. Same seed gives byte-identical files: `MANIFEST.json` holds the
sha256 and row count of each file for seed 42, and a test compares it to a fresh run.

| Table | Shape kept from the platform | Quirk the recipe handles |
|---|---|---|
| `google_ads_campaign_stats` | BigQuery Data Transfer Service columns, API path with dots as underscores | cost in `metrics_cost_micros` (INT64 micros) |
| `dv360_impressions` | Data Transfer v2 impression rows, one per impression | money in nanos, 3 currency variants (USD, Partner, Advertiser); the documented names have spaces, so a column is the name lower-cased with other characters as `_` |
| `meta_insights` | Marketing API insights | every number is a string; `actions` is an array of `{action_type, value}` with string values |
| `tiktok_integrated_report` | integrated report, dimensions `stat_time_day` + `campaign_id` | metrics are strings; `stat_time_day` is `yyyy-MM-dd HH:mm:ss` |
| `amazon_dsp_campaign` | Reporting v3 names (`clickThroughs`, `totalCost`, `purchases14d`...) | `synth/amazon_v3_to_v1.json` maps them to the v1 field IDs, read on each v1 field page (line "Version 3 Reporting Name (DSP)") |
| `ttd_impressions`, `ttd_clicks` | REDS feeds; clicks have all 16 documented columns | timestamps are `yyyy-MM-ddTHH:mm:ss` text; cost is in USD with the exchange rate on the row |

Planted traps (added on purpose by the generator, not measured frequencies), each pinned by a test:

| Trap | Where | Count with seed 42 |
|---|---|---|
| Duplicate click lines: same `ClickRedirectId`, later `ProcessedTime` | `ttd_clicks` | 20 duplicate rows on 998 clicks (2 %) |
| Empty string instead of `"0"` for `spend` on a day without delivery | `meta_insights` | 3 rows (3 campaigns), 2026-10-27 |
| Day boundaries in Europe/Paris | Meta, TikTok | the other tables use UTC days |

Conventions of recipe 14: spend is EUR (Google micros / 1e6, DV360 Advertiser Currency nanos /
1e9, TTD USD cost times its exchange rate); each platform's day is kept as it reports it, because
day-level rows cannot be moved to another timezone; the generator writes only the DV360 impression file (Data Transfer
also has click and activity files), so DV360 clicks stay NULL. The DV360 and TTD tables are log samples (about 400 impressions per campaign and day),
which is why their spend is small next to the day-level platforms.

The truth numbers in the recipe headers (`-- TRUTH` lines) come from `synth/truth.py`, which
recomputes them in plain Python from the generated files; the tests recompute them again.

Bytes read on BigQuery (dry run): recipe 14 naive 1.8 MB, fixed 2.4 MB; recipe 15 naive 0.3 MB,
fixed 1.6 MB. The free sandbox is enough.

## What is not tested

- Nothing here runs against Ads Data Hub, a Google Ads account or a real CRM. The fixture
  recipes (01 to 04, 05b, 06, 08, 10, 11) and the synthetic ones (14, 15) prove the logic; they say nothing about how often
  each bug shows up in production.
- The public GA4 sample has no `collected_traffic_source` field, so 05b uses a fixture with
  that part of the export schema.
- The `gcd` / `gcs` values in 04 are illustrative strings: the recipe extracts them and does
  not decode them.
- Frozen results reflect BigQuery as of November 2026.

## Data

Fixtures are small and invented. The public dataset is Google's obfuscated GA4 sample. No
client data.

## License

MIT, see `LICENSE`.
