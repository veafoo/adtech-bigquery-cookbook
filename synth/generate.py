#!/usr/bin/env python3
"""Seeded synthetic cross-channel ad data: one fictional advertiser, 3 campaigns per platform.

One NDJSON file per table. Each table keeps the field names and the native quirks of its
platform's export (micros, nanos, numbers as strings, local-time days, duplicated log lines),
so the recipes in recipes/14 and 15 have something real to fix. Same seed, same bytes.

  python3 synth/generate.py [--seed 42] [--days 28] [--out synth/]

Field names come from the official documentation pages (collected 2026-11-01); the URL of
each platform page is above its field list below. Nothing here is real client data.
Tables, their traps and the money conventions are described in README.md ("Layer 2").
"""
import argparse
import hashlib
import json
import random
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
START = date(2026, 10, 1)
DEFAULT_SEED = 42
DEFAULT_DAYS = 28
EVENTS_PER_DAY = (280, 520)   # DV360 / TTD impression log lines per campaign and day
DUP_RATE = 0.02               # share of TTD click rows that come back a second time
ADVERTISER_CURRENCY = "EUR"   # USD -> EUR rate varies per day; Partner Currency in DV360 is GBP

# Day boundaries of each table: the platform's own reporting timezone (documented in README).
TIMEZONES = {
    "google_ads_campaign_stats": "UTC",
    "dv360_impressions": "UTC",
    "meta_insights": "Europe/Paris",
    "tiktok_integrated_report": "Europe/Paris",
    "amazon_dsp_campaign": "UTC",
    "ttd_impressions": "UTC",
    "ttd_clicks": "UTC",
}

# --- field lists: (documented name, BigQuery type) ---------------------------------------------
# Google Ads, BigQuery Data Transfer Service, table p_ads_CampaignBasicStats_<customer_id>.
# https://docs.cloud.google.com/bigquery/docs/google-ads-transformation
# Column = API field path with dots replaced by underscores; types from the GAQL field pages
# (https://developers.google.com/google-ads/api/fields/v26/metrics): INT64 / DOUBLE.
GOOGLE_ADS = [
    ("customer_id", "INT64"), ("campaign_id", "INT64"), ("segments_date", "DATE"),
    ("metrics_impressions", "INT64"), ("metrics_clicks", "INT64"),
    ("metrics_cost_micros", "INT64"), ("metrics_conversions", "FLOAT64"),
    ("metrics_conversions_value", "FLOAT64"),
]
# DV360, Data Transfer v2 impression file. Money fields are Long, in nanos, in 3 currency
# variants (USD / Partner Currency / Advertiser Currency).
# https://developers.google.com/bid-manager/dtv2/reference/file-format
# The documented names contain spaces and brackets, which BigQuery columns cannot: the column
# is the name lower-cased with every run of other characters replaced by one underscore.
DV360 = [
    ("DV360 Auction ID", "STRING"), ("DV360 Request Time", "INT64"),
    ("DV360 Advertiser ID", "INT64"), ("DV360 Insertion Order ID", "INT64"),
    ("DV360 Line Item ID", "INT64"), ("DV360 Campaign ID", "INT64"),
    ("DV360 Creative ID", "INT64"), ("DV360 Country Code", "STRING"),
    ("DV360 Device Type", "INT64"), ("DV360 Media Cost (USD)", "INT64"),
    ("DV360 Media Cost (Partner Currency)", "INT64"),
    ("DV360 Media Cost (Advertiser Currency)", "INT64"),
]
# Meta Marketing API, Insights. All numbers come back as strings.
# https://developers.facebook.com/docs/marketing-api/reference/ad-campaign-group/insights/
# `actions` is a list of AdsActionStats: https://developers.facebook.com/docs/marketing-api/reference/ads-action-stats/
META = [
    ("account_id", "STRING"), ("campaign_id", "STRING"), ("campaign_name", "STRING"),
    ("date_start", "STRING"), ("date_stop", "STRING"), ("impressions", "STRING"),
    ("clicks", "STRING"), ("spend", "STRING"), ("account_currency", "STRING"),
    ("actions", "RECORD"),
]
META_ACTIONS = [("action_type", "STRING"), ("value", "STRING")]
# TikTok Marketing API v1.3, GET /report/integrated/get/: metrics are strings. The format of
# stat_time_day ("yyyy-MM-dd HH:mm:ss") is the one in the example response of the "Run a synchronous report" page.
# https://business-api.tiktok.com/portal/docs/basic-reports-supported-dimensions/v1.3
# https://business-api.tiktok.com/portal/docs/basic-reports-supported-metrics/v1.3
TIKTOK = [
    ("advertiser_id", "STRING"), ("campaign_id", "STRING"), ("stat_time_day", "STRING"),
    ("spend", "STRING"), ("impressions", "STRING"), ("clicks", "STRING"),
    ("currency", "STRING"),
]
# Amazon Ads DSP reporting v3, report type CAMPAIGN (dimension ORDER), timeUnit DAILY.
# https://advertising.amazon.com/API/docs/en-us/reporting/dsp/report-types
AMAZON = [
    ("date", "STRING"), ("orderId", "INT64"), ("orderName", "STRING"),
    ("orderCurrency", "STRING"), ("impressions", "INT64"), ("clickThroughs", "INT64"),
    ("totalCost", "NUMERIC"), ("purchases14d", "INT64"), ("sales14d", "NUMERIC"),
]
# The Trade Desk REDS, Clicks feed (16 columns) and a subset of the Impressions feed.
# https://open.thetradedesk.com/advertiser/docsApp/GuidesAdvertiser/reds/doc/Clicks
# https://open.thetradedesk.com/advertiser/docsApp/GuidesAdvertiser/reds/doc/Impressions
TTD_CLICKS = [
    ("LogEntryTime", "STRING"), ("ClickRedirectId", "STRING"), ("IPAddress", "STRING"),
    ("ReferrerUrl", "STRING"), ("RedirectUrl", "STRING"), ("CampaignId", "STRING"),
    ("ChannelId", "STRING"), ("AdvertiserId", "STRING"), ("DisplayImpressionId", "STRING"),
    ("Keyword", "STRING"), ("KeywordId", "STRING"), ("MatchType", "STRING"),
    ("DistributionNetwork", "STRING"), ("RawUrl", "STRING"), ("ProcessedTime", "STRING"),
    ("DeviceType", "INT64"),
]
TTD_IMPRESSIONS = [
    ("LogEntryTime", "STRING"), ("ImpressionId", "STRING"), ("PartnerId", "STRING"),
    ("AdvertiserId", "STRING"), ("CampaignId", "STRING"), ("AdGroupId", "STRING"),
    ("CreativeId", "STRING"), ("AdFormat", "STRING"), ("Site", "STRING"),
    ("DeviceType", "INT64"), ("CountryLong", "STRING"),
    ("AdvertiserCostInUSDollars", "NUMERIC"), ("AdvertiserCurrency", "STRING"),
    ("AdvertiserCurrencyExchangeRateFromUSD", "NUMERIC"), ("ProcessedTime", "STRING"),
]


def bq_name(documented):
    """Documented field name -> BigQuery column name (see the DV360 comment above)."""
    return re.sub(r"[^a-z0-9]+", "_", documented.lower()).strip("_")


FIELDS = {
    "google_ads_campaign_stats": GOOGLE_ADS,
    "dv360_impressions": DV360,
    "meta_insights": META,
    "tiktok_integrated_report": TIKTOK,
    "amazon_dsp_campaign": AMAZON,
    "ttd_impressions": TTD_IMPRESSIONS,
    "ttd_clicks": TTD_CLICKS,
}
# Column names as stored; only DV360 renames its documented fields.
COLUMNS = {t: [bq_name(n) if t == "dv360_impressions" else n for n, _ in f]
           for t, f in FIELDS.items()}


def schema_json(table):
    """Explicit BigQuery load schema (no autodetect): string-typed platform fields stay STRING."""
    cols = []
    for (doc, typ), name in zip(FIELDS[table], COLUMNS[table]):
        if typ == "RECORD":
            cols.append({"name": name, "type": "RECORD", "mode": "REPEATED",
                         "fields": [{"name": n, "type": t, "mode": "NULLABLE"} for n, t in META_ACTIONS]})
        else:
            cols.append({"name": name, "type": typ, "mode": "NULLABLE"})
    return cols


# --- generator ----------------------------------------------------------------------------------
def rng_for(seed, name):
    """One independent, seed-stable random stream per table (str seeds are hashed, not hash()-ed)."""
    return random.Random(f"{seed}:{name}")


def guid(rng):
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def cents(c):
    return f"{c // 100}.{c % 100:02d}"


def campaigns(prefix, base, names):
    return [{"id": base + i, "name": f"synth_{prefix}_{n}", "key": f"{prefix}{i + 1}"}
            for i, n in enumerate(names)]


GOOGLE_C = campaigns("google", 1100000, ["search_brand", "search_generic", "pmax"])
DV360_C = campaigns("dv360", 2200000, ["prospecting", "retargeting", "video"])
META_C = campaigns("meta", 120000000000, ["prospecting", "retargeting", "catalog"])
TIKTOK_C = campaigns("tiktok", 170000000000, ["spark_ads", "collection", "reach"])
AMAZON_C = campaigns("amazon", 5100000, ["sponsored_display", "streaming_tv", "retargeting"])
TTD_C = [{"id": f"ttdcmp{i + 1}", "name": n} for i, n in enumerate(["ctv", "display", "audio"])]

CUSTOMER_ID = 4400110011
DV360_ADVERTISER = 9900220022
META_ACCOUNT = "act_330033003300"
TIKTOK_ADVERTISER = "7700440044004"
TTD_ADVERTISER = "synthadv1"
TTD_PARTNER = "synthpartner1"


def day_list(days):
    return [START + timedelta(days=i) for i in range(days)]


def fx_rates(seed, days):
    """Per day: USD->EUR and USD->GBP, integers in 1/10000."""
    r = rng_for(seed, "fx")
    return [{"eur": r.randint(9000, 9400), "gbp": r.randint(7600, 8000)} for _ in range(days)]


def pick_meta_zero_day(seed, days):
    """The day Meta returns spend "" (true value 0): never the first or last day."""
    return START + timedelta(days=rng_for(seed, "meta_zero_day").randint(1, days - 2))


def gen_google(seed, days):
    r = rng_for(seed, "google_ads_campaign_stats")
    rows = []
    for d in day_list(days):
        for c in GOOGLE_C:
            imp = r.randint(1500, 4000)
            clicks = int(imp * r.uniform(0.02, 0.05))
            cost_micros = clicks * r.randint(30, 90) * 10_000        # EUR 0.30-0.90 per click
            conv = round(clicks * r.uniform(0.01, 0.04), 2)
            rows.append({"customer_id": CUSTOMER_ID, "campaign_id": c["id"],
                         "segments_date": d.isoformat(), "metrics_impressions": imp,
                         "metrics_clicks": clicks, "metrics_cost_micros": cost_micros,
                         "metrics_conversions": conv,
                         "metrics_conversions_value": round(conv * r.randint(40, 90), 2)})
    return rows


def event_times(r, d, n):
    """n sorted second-resolution offsets inside UTC day d, as datetimes."""
    base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return [base + timedelta(seconds=s) for s in sorted(r.randint(0, 86399) for _ in range(n))]


def gen_dv360(seed, days):
    r = rng_for(seed, "dv360_impressions")
    fx = fx_rates(seed, days)
    rows = []
    for i, d in enumerate(day_list(days)):
        for k, c in enumerate(DV360_C):
            for t in event_times(r, d, r.randint(*EVENTS_PER_DAY)):
                usd = r.randint(3_000_000, 8_000_000)               # nanos, CPM USD 3-8
                micros = int(t.timestamp()) * 1_000_000 + r.randrange(1_000_000)
                rows.append({
                    "dv360_auction_id": guid(r), "dv360_request_time": micros,
                    "dv360_advertiser_id": DV360_ADVERTISER,
                    "dv360_insertion_order_id": 3300000 + k, "dv360_line_item_id": 3400000 + k,
                    "dv360_campaign_id": c["id"], "dv360_creative_id": 3500000 + k * 10 + r.randint(0, 2),
                    "dv360_country_code": r.choice(["FR", "FR", "FR", "BE", "DE"]),
                    "dv360_device_type": r.choice([0, 0, 1, 2]),
                    "dv360_media_cost_usd": usd,
                    "dv360_media_cost_partner_currency": usd * fx[i]["gbp"] // 10_000,
                    "dv360_media_cost_advertiser_currency": usd * fx[i]["eur"] // 10_000,
                })
    return rows


def gen_meta(seed, days):
    r = rng_for(seed, "meta_insights")
    zero_day = pick_meta_zero_day(seed, days)
    rows = []
    for d in day_list(days):
        for c in META_C:
            row = {"account_id": META_ACCOUNT[4:], "campaign_id": str(c["id"]),
                   "campaign_name": c["name"], "date_start": d.isoformat(),
                   "date_stop": d.isoformat()}
            imp = r.randint(2000, 6000)
            clicks = int(imp * r.uniform(0.01, 0.025))
            spend_c = imp * r.randint(400, 900) // 1000
            purchases = int(clicks * r.uniform(0.01, 0.05))
            if d == zero_day:       # no delivery: the API sends "" instead of "0" for spend
                row.update({"impressions": "0", "clicks": "0", "spend": ""})
            else:
                row.update({"impressions": str(imp), "clicks": str(clicks), "spend": cents(spend_c)})
            row["account_currency"] = ADVERTISER_CURRENCY
            if d != zero_day:
                acts = [{"action_type": "link_click", "value": str(clicks)}]
                if purchases:
                    acts.append({"action_type": "offsite_conversion.fb_pixel_purchase",
                                 "value": str(purchases)})
                row["actions"] = acts
            rows.append(row)
    return rows


def gen_tiktok(seed, days):
    r = rng_for(seed, "tiktok_integrated_report")
    rows = []
    for d in day_list(days):
        for c in TIKTOK_C:
            imp = r.randint(3000, 9000)
            clicks = int(imp * r.uniform(0.006, 0.018))
            rows.append({"advertiser_id": TIKTOK_ADVERTISER, "campaign_id": str(c["id"]),
                         "stat_time_day": f"{d.isoformat()} 00:00:00",
                         "spend": cents(imp * r.randint(200, 600) // 1000),
                         "impressions": str(imp), "clicks": str(clicks),
                         "currency": ADVERTISER_CURRENCY})
    return rows


def gen_amazon(seed, days):
    r = rng_for(seed, "amazon_dsp_campaign")
    rows = []
    for d in day_list(days):
        for c in AMAZON_C:
            imp = r.randint(1500, 5000)
            clicks = int(imp * r.uniform(0.003, 0.009))
            cost_c = clicks * r.randint(40, 120)
            purchases = int(clicks * r.uniform(0.03, 0.12))
            rows.append({"date": d.isoformat(), "orderId": c["id"], "orderName": c["name"],
                         "orderCurrency": ADVERTISER_CURRENCY, "impressions": imp,
                         "clickThroughs": clicks, "totalCost": cost_c / 100,
                         "purchases14d": purchases,
                         "sales14d": purchases * r.randint(2500, 7000) / 100})
    return rows


def ttd_ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def gen_ttd(seed, days):
    """Returns (impressions, clicks, number of duplicated click rows)."""
    r = rng_for(seed, "ttd")
    fx = fx_rates(seed, days)
    imps, clicks = [], []
    ctr = {c["id"]: p for c, p in zip(TTD_C, (0.02, 0.03, 0.04))}
    for i, d in enumerate(day_list(days)):
        end_of_day = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)
        for k, c in enumerate(TTD_C):
            for t in event_times(r, d, r.randint(*EVENTS_PER_DAY)):
                imp_id = guid(r)
                device = r.choice([1, 2, 3, 4])
                imps.append({
                    "LogEntryTime": ttd_ts(t), "ImpressionId": imp_id, "PartnerId": TTD_PARTNER,
                    "AdvertiserId": TTD_ADVERTISER, "CampaignId": c["id"],
                    "AdGroupId": f"synthadg{k + 1}", "CreativeId": f"synthcr{k + 1}{r.randint(1, 3)}",
                    "AdFormat": r.choice(["300x250", "728x90", "320x50"]),
                    "Site": r.choice(["example-news.test", "example-sport.test", "example-mail.test"]),
                    "DeviceType": device, "CountryLong": r.choice(["France", "France", "Belgium"]),
                    "AdvertiserCostInUSDollars": r.randint(200, 900) / 100_000,
                    "AdvertiserCurrency": ADVERTISER_CURRENCY,
                    "AdvertiserCurrencyExchangeRateFromUSD": fx[i]["eur"] / 10_000,
                    "ProcessedTime": ttd_ts(t + timedelta(seconds=r.randint(60, 600))),
                })
                if r.random() < ctr[c["id"]]:
                    ct = min(t + timedelta(seconds=r.randint(5, 900)), end_of_day)
                    cid = guid(r)
                    clicks.append({
                        "LogEntryTime": ttd_ts(ct), "ClickRedirectId": cid,
                        "IPAddress": f"198.51.100.{r.randint(1, 254)}",
                        "ReferrerUrl": f"https://{imps[-1]['Site']}/article-{r.randint(1, 500)}",
                        "RedirectUrl": f"https://track.example.test/redirect/{cid}",
                        "CampaignId": c["id"], "ChannelId": None, "AdvertiserId": TTD_ADVERTISER,
                        "DisplayImpressionId": imp_id, "Keyword": None, "KeywordId": None,
                        "MatchType": None, "DistributionNetwork": None,
                        "RawUrl": f"https://track.example.test/click?cid={cid}",
                        "ProcessedTime": ttd_ts(ct + timedelta(seconds=r.randint(60, 600))),
                        "DeviceType": device,
                    })
    # Trap: ~2 % of the click lines are reprocessed later: same ClickRedirectId, later ProcessedTime.
    n_dup = round(DUP_RATE * len(clicks))
    for idx in sorted(r.sample(range(len(clicks)), n_dup)):
        orig = clicks[idx]
        later = datetime.strptime(orig["ProcessedTime"], "%Y-%m-%dT%H:%M:%S") + timedelta(
            seconds=r.randint(3600, 7200))
        clicks.append({**orig, "ProcessedTime": ttd_ts(later)})
    clicks.sort(key=lambda x: (x["LogEntryTime"], x["ClickRedirectId"], x["ProcessedTime"]))
    return imps, clicks, n_dup


def build(seed=DEFAULT_SEED, days=DEFAULT_DAYS):
    """All tables as {table: [row dicts]} plus the facts about the planted traps."""
    if days < 4:
        raise ValueError("days must be at least 4")
    imps, clicks, n_dup = gen_ttd(seed, days)
    tables = {
        "google_ads_campaign_stats": gen_google(seed, days),
        "dv360_impressions": gen_dv360(seed, days),
        "meta_insights": gen_meta(seed, days),
        "tiktok_integrated_report": gen_tiktok(seed, days),
        "amazon_dsp_campaign": gen_amazon(seed, days),
        "ttd_impressions": imps,
        "ttd_clicks": clicks,
    }
    zero_day = pick_meta_zero_day(seed, days)
    traps = {
        "ttd_clicks_unique": len(clicks) - n_dup,
        "ttd_clicks_duplicate_rows": n_dup,
        "meta_empty_spend_date": zero_day.isoformat(),
        "meta_empty_spend_rows": sum(1 for r in tables["meta_insights"] if r["spend"] == ""),
    }
    return tables, traps


def dumps_line(row):
    return json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n"


def write(out, seed=DEFAULT_SEED, days=DEFAULT_DAYS):
    """Write <table>.ndjson files and MANIFEST.json; return the manifest dict."""
    tables, traps = build(seed, days)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for table, rows in tables.items():
        extra = set().union(*(r.keys() for r in rows)) - set(COLUMNS[table])
        if extra:
            raise RuntimeError(f"{table}: undocumented columns {sorted(extra)}")
        data = "".join(dumps_line(r) for r in rows).encode("ascii")
        (out / f"{table}.ndjson").write_bytes(data)
        files[f"{table}.ndjson"] = {"sha256": hashlib.sha256(data).hexdigest(), "rows": len(rows)}
    manifest = {"seed": seed, "days": days, "start": START.isoformat(), "files": files,
                "traps": traps, "timezones": TIMEZONES}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def write_schemas(out=HERE / "schemas"):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for table in FIELDS:
        (out / f"{table}.json").write_text(json.dumps(schema_json(table), indent=1) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--write-schemas", action="store_true",
                    help="rewrite synth/schemas/*.json from the field lists above")
    a = ap.parse_args(argv)
    if a.write_schemas:
        write_schemas()
    m = write(a.out, a.seed, a.days)
    for name, f in m["files"].items():
        print(f"{name}: {f['rows']} rows, sha256 {f['sha256'][:12]}")
    print("traps:", json.dumps(m["traps"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
