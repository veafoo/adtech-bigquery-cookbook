#!/usr/bin/env python3
"""Ground truth for recipes 14 and 15, recomputed in plain Python from the generated NDJSON.

The numbers in the headers of recipes/14_*.sql and 15_*.sql come from here, and the tests
recompute them again from freshly generated files. No SQL, no BigQuery.

  python3 synth/truth.py [DIR]      # prints the `-- TRUTH` lines for DIR (default: synth/)
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENT = Decimal("0.01")
CHANNELS = ["google_ads", "dv360", "meta", "tiktok", "amazon_dsp", "ttd"]


def load(dirpath, table):
    """Rows of <table>.ndjson with every JSON number kept exact (Decimal)."""
    with open(Path(dirpath) / f"{table}.ndjson") as f:
        return [json.loads(line, parse_float=Decimal) for line in f]


def money(x):
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def num(s):
    """Meta/TikTok string -> Decimal; the empty string Meta sends for a zero spend -> 0."""
    return Decimal(s) if s != "" else Decimal(0)


def utc_day(micros):
    return datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc).date().isoformat()


def daily_rows(dirpath):
    """{(date, channel): [spend_eur (exact), impressions, clicks or None]}.

    Convention: Meta and TikTok days are Europe/Paris days as the platforms report them and are
    not shifted; Google Ads, Amazon, DV360 and TTD days are UTC days.
    """
    out = defaultdict(lambda: [Decimal(0), 0, None])

    def add(day, ch, spend, imps, clicks):
        r = out[(day, ch)]
        r[0] += spend
        r[1] += imps
        if clicks is not None:
            r[2] = (r[2] or 0) + clicks

    for r in load(dirpath, "google_ads_campaign_stats"):
        add(r["segments_date"], "google_ads", Decimal(r["metrics_cost_micros"]) / 1_000_000,
            r["metrics_impressions"], r["metrics_clicks"])
    for r in load(dirpath, "dv360_impressions"):
        add(utc_day(r["dv360_request_time"]), "dv360",
            Decimal(r["dv360_media_cost_advertiser_currency"]) / 1_000_000_000, 1, None)
    for r in load(dirpath, "meta_insights"):
        add(r["date_start"], "meta", num(r["spend"]), int(r["impressions"]), int(r["clicks"]))
    for r in load(dirpath, "tiktok_integrated_report"):
        add(r["stat_time_day"][:10], "tiktok", num(r["spend"]), int(r["impressions"]), int(r["clicks"]))
    for r in load(dirpath, "amazon_dsp_campaign"):
        add(r["date"], "amazon_dsp", r["totalCost"], r["impressions"], r["clickThroughs"])
    for r in load(dirpath, "ttd_impressions"):
        add(r["LogEntryTime"][:10], "ttd",
            r["AdvertiserCostInUSDollars"] * r["AdvertiserCurrencyExchangeRateFromUSD"], 1, None)
    for r in dedup_clicks(load(dirpath, "ttd_clicks")):
        add(r["LogEntryTime"][:10], "ttd", Decimal(0), 0, 1)
    return out


def dedup_clicks(clicks):
    """One row per ClickRedirectId, the earliest ProcessedTime (ISO strings sort as times)."""
    best = {}
    for r in clicks:
        k = r["ClickRedirectId"]
        if k not in best or r["ProcessedTime"] < best[k]["ProcessedTime"]:
            best[k] = r
    return list(best.values())


def truth_14(dirpath):
    """{name: {key: str}}: totals per channel over the whole window, the grand total, and the
    Meta row of the day that returns spend as an empty string. Spend is the sum of the daily
    rows, each rounded to cents like the recipe's output table."""
    rows = daily_rows(dirpath)
    per = {c: [Decimal(0), 0, None] for c in CHANNELS}
    for (day, ch), (s, i, c) in rows.items():
        per[ch][0] += money(s)
        per[ch][1] += i
        if c is not None:
            per[ch][2] = (per[ch][2] or 0) + c
    out = {}
    for ch in CHANNELS:
        s, i, c = per[ch]
        out[ch] = {"spend_eur": str(money(s)), "impressions": str(i),
                   "clicks": "null" if c is None else str(c)}
    out["total"] = {"spend_eur": str(money(sum(per[c][0] for c in CHANNELS))),
                    "impressions": str(sum(per[c][1] for c in CHANNELS)),
                    "clicks": str(sum(per[c][2] or 0 for c in CHANNELS))}
    empty = sorted({r["date_start"] for r in load(dirpath, "meta_insights") if r["spend"] == ""})
    assert len(empty) == 1, empty
    d = empty[0]
    s, i, c = rows[(d, "meta")]
    out["meta_empty_day"] = {"date": d, "spend_eur": str(money(s)), "impressions": str(i),
                             "clicks": str(c)}
    return out


def truth_15(dirpath):
    """Per TTD campaign: impressions, raw click rows, deduplicated clicks, both CTRs (4 decimals);
    plus the number of duplicate rows."""
    imps = defaultdict(int)
    for r in load(dirpath, "ttd_impressions"):
        imps[r["CampaignId"]] += 1
    raw, ded = defaultdict(int), defaultdict(int)
    clicks = load(dirpath, "ttd_clicks")
    for r in clicks:
        raw[r["CampaignId"]] += 1
    for r in dedup_clicks(clicks):
        ded[r["CampaignId"]] += 1
    q = Decimal("0.0001")
    out = {}
    for c in sorted(imps):
        out[c] = {"impressions": str(imps[c]), "clicks_raw": str(raw[c]),
                  "clicks_dedup": str(ded[c]),
                  "ctr_raw": str((Decimal(raw[c]) / imps[c]).quantize(q, rounding=ROUND_HALF_UP)),
                  "ctr_dedup": str((Decimal(ded[c]) / imps[c]).quantize(q, rounding=ROUND_HALF_UP))}
    out["duplicate_rows"] = {"rows": str(len(clicks) - len(dedup_clicks(clicks)))}
    return out


def lines(truth):
    return [f"-- TRUTH {name} " + " ".join(f"{k}={v}" for k, v in d.items())
            for name, d in truth.items()]


def parse_truth(text):
    """Read the `-- TRUTH <name> k=v ...` lines of a recipe header into {name: {k: v}}."""
    out = {}
    for line in text.splitlines():
        if line.startswith("-- TRUTH "):
            name, *pairs = line[len("-- TRUTH "):].split()
            out[name] = dict(p.split("=", 1) for p in pairs)
    return out


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else HERE
    print("\n".join(["-- recipe 14"] + lines(truth_14(d)) + ["-- recipe 15"] + lines(truth_15(d))))
