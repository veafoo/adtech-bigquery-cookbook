#!/usr/bin/env python3
"""Tests for the synthetic data generator (synth/generate.py) and its ground truth.

Run from the repo root:  python3 -m unittest synth.test_generate -v
(the whole suite:        python3 -m unittest test_cookbook synth.test_generate)
"""
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from synth import generate, truth

ROOT = Path(__file__).resolve().parent.parent
SYNTH = ROOT / "synth"
SOURCES = ROOT / "schemas" / "SOURCES.md"   # gitignored: tests that need it skip on a clone

# Field names as printed in schemas/SOURCES.md (official pages, 2026-11-01), copied here on
# purpose: the generator must not be able to drift from the docs unnoticed.
DOCUMENTED = {
    # section 8b, CampaignBasicStats (subset used)
    "google_ads_campaign_stats": {
        "customer_id", "campaign_id", "segments_date", "metrics_impressions", "metrics_clicks",
        "metrics_cost_micros", "metrics_conversions", "metrics_conversions_value"},
    # section 2, plus the 3 currency variants named in its intro (USD / Partner / Advertiser Currency)
    "dv360_impressions": {
        "DV360 Auction ID", "DV360 Request Time", "DV360 Advertiser ID",
        "DV360 Insertion Order ID", "DV360 Line Item ID", "DV360 Campaign ID",
        "DV360 Creative ID", "DV360 Country Code", "DV360 Device Type",
        "DV360 Media Cost (USD)", "DV360 Media Cost (Partner Currency)",
        "DV360 Media Cost (Advertiser Currency)"},
    # section 3a
    "meta_insights": {
        "account_id", "campaign_id", "campaign_name", "date_start", "date_stop", "impressions",
        "clicks", "spend", "account_currency", "actions"},
    # section 4
    "tiktok_integrated_report": {
        "advertiser_id", "campaign_id", "stat_time_day", "spend", "impressions", "clicks",
        "currency"},
    # section 5 / schemas/amazon-dsp.md (orderName and orderCurrency: the Order dimension)
    "amazon_dsp_campaign": {
        "date", "orderId", "orderName", "orderCurrency", "impressions", "clickThroughs",
        "totalCost", "purchases14d", "sales14d"},
    # section 7, Impressions feed (subset)
    "ttd_impressions": {
        "LogEntryTime", "ImpressionId", "PartnerId", "AdvertiserId", "CampaignId", "AdGroupId",
        "CreativeId", "AdFormat", "Site", "DeviceType", "CountryLong",
        "AdvertiserCostInUSDollars", "AdvertiserCurrency",
        "AdvertiserCurrencyExchangeRateFromUSD", "ProcessedTime"},
    # section 7, Clicks feed: all 16 columns
    "ttd_clicks": {
        "LogEntryTime", "ClickRedirectId", "IPAddress", "ReferrerUrl", "RedirectUrl",
        "CampaignId", "ChannelId", "AdvertiserId", "DisplayImpressionId", "Keyword", "KeywordId",
        "MatchType", "DistributionNetwork", "RawUrl", "ProcessedTime", "DeviceType"},
}

# SOURCES.md section 5, table "Core CAMPAIGN fields", column "v1 field ID" (parentheses are
# the page's own notes). None = "not seen under this v3 name".
AMAZON_V1 = {
    "impressions": ["metric.impressions"],
    "clickThroughs": ["metric.clicks"],
    "totalCost": ["metric.totalCost", "metric.totalCostConverted"],
    "totalFee": ["metric.totalFees", "metric.totalFeesConverted"],
    "eCPM": ["metric.cpm"],
    "CTR": ["metric.ctr"],
    "purchases14d": ["metric.purchasesPromoted"],
    "purchasesClicks14d": ["metric.purchasesFromClicksPromoted"],
    "sales14d": ["metric.salesPromoted"],
    "ROAS14d": None,
    "dpv14d": None,
    "orderId": ["campaign.id"],
    "lineItemId": ["adGroup.id"],
    "creativeID": ["ad.id"],
    "date": ["date.value"],
}

TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Generated(unittest.TestCase):
    """Seed 42, 28 days, generated once for the whole class."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.manifest = generate.write(cls.dir)
        cls.rows = {t: truth.load(cls.dir, t) for t in generate.FIELDS}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class Determinism(Generated):
    def test_same_seed_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as other:
            m2 = generate.write(other)
            self.assertEqual(m2, self.manifest)
            for name in self.manifest["files"]:
                self.assertEqual(sha(Path(other) / name), sha(self.dir / name), name)

    def test_different_seed_changes_every_file(self):
        with tempfile.TemporaryDirectory() as other:
            m2 = generate.write(other, seed=7)
            for name, f in self.manifest["files"].items():
                self.assertNotEqual(m2["files"][name]["sha256"], f["sha256"], name)

    def test_manifest_hashes_and_counts_match_the_files(self):
        for name, f in self.manifest["files"].items():
            self.assertEqual(sha(self.dir / name), f["sha256"])
            self.assertEqual(len((self.dir / name).read_text().splitlines()), f["rows"])

    def test_committed_manifest_pins_seed_42(self):
        # Regression: the committed MANIFEST.json is what `generate.py` writes for seed 42, so a
        # changed generator (or a changed Python random stream) shows up as a diff, not silently.
        self.assertEqual(json.loads((SYNTH / "MANIFEST.json").read_text()), self.manifest)


class Columns(Generated):
    def test_every_column_is_documented(self):
        for table, names in DOCUMENTED.items():
            if table == "dv360_impressions":
                names = {generate.bq_name(n) for n in names}
            self.assertEqual(set(generate.COLUMNS[table]), names, table)
            used = set().union(*(r.keys() for r in self.rows[table]))
            self.assertLessEqual(used, names, table)

    def test_documented_names_appear_in_sources(self):
        if not SOURCES.exists():
            self.skipTest("schemas/SOURCES.md is not in the repo (gitignored)")
        # the Amazon field list lives in amazon-dsp.md, next to SOURCES.md
        text = SOURCES.read_text() + (SOURCES.parent / "amazon-dsp.md").read_text()
        for table, names in DOCUMENTED.items():
            for n in names:
                # a currency variant: the page lists "<name> (3 currencies)"
                base = re.sub(r" \((USD|Partner Currency|Advertiser Currency)\)$", "", n)
                self.assertIn(base, text, f"{table}.{n}")

    def test_schema_files_are_the_generator_schemas(self):
        for table in generate.FIELDS:
            on_disk = json.loads((SYNTH / "schemas" / f"{table}.json").read_text())
            self.assertEqual(on_disk, generate.schema_json(table), table)
            self.assertEqual([c["name"] for c in on_disk], generate.COLUMNS[table], table)

    def test_string_typed_platform_fields_stay_string(self):
        types = {c["name"]: c["type"] for t in ("meta_insights", "tiktok_integrated_report")
                 for c in generate.schema_json(t)}
        for col in ("impressions", "clicks", "spend", "date_start", "stat_time_day"):
            self.assertEqual(types[col], "STRING", col)
        self.assertNotIn("--autodetect", (SYNTH / "load.sh").read_text())

    def test_platform_value_shapes(self):
        for r in self.rows["meta_insights"]:
            for k in ("impressions", "clicks", "spend"):
                self.assertIsInstance(r[k], str)
            for a in r.get("actions", []):
                self.assertIsInstance(a["value"], str)
        for r in self.rows["tiktok_integrated_report"]:
            for k in ("spend", "impressions", "clicks"):
                self.assertIsInstance(r[k], str)
            self.assertRegex(r["stat_time_day"], r"^\d{4}-\d\d-\d\d 00:00:00$")
        for r in self.rows["google_ads_campaign_stats"]:
            self.assertIsInstance(r["metrics_cost_micros"], int)
            self.assertEqual(r["metrics_cost_micros"] % 10_000, 0)
        for r in self.rows["dv360_impressions"]:
            self.assertIsInstance(r["dv360_media_cost_usd"], int)
            self.assertLess(r["dv360_media_cost_usd"], 10_000_000)  # nanos of a sub-cent CPM
        for t in ("ttd_clicks", "ttd_impressions"):
            for r in self.rows[t]:
                self.assertRegex(r["LogEntryTime"], TS)
                self.assertRegex(r["ProcessedTime"], TS)

    def test_row_counts(self):
        # 28 days x 3 campaigns for the day-level platforms
        for t in ("google_ads_campaign_stats", "meta_insights", "tiktok_integrated_report",
                  "amazon_dsp_campaign"):
            self.assertEqual(len(self.rows[t]), 28 * 3, t)


class Traps(Generated):
    def test_ttd_duplicate_clicks(self):
        clicks = self.rows["ttd_clicks"]
        by_id = {}
        for r in clicks:
            by_id.setdefault(r["ClickRedirectId"], []).append(r)
        dups = {k: v for k, v in by_id.items() if len(v) > 1}
        unique = len(by_id)
        self.assertEqual(self.manifest["traps"]["ttd_clicks_unique"], unique)
        self.assertEqual(len(dups), round(0.02 * unique))              # 20 of 998
        self.assertEqual(self.manifest["traps"]["ttd_clicks_duplicate_rows"], len(dups))
        self.assertEqual(len(clicks), unique + len(dups))
        for rs in dups.values():
            self.assertEqual(len(rs), 2)
            first, second = sorted(rs, key=lambda r: r["ProcessedTime"])
            self.assertLess(first["ProcessedTime"], second["ProcessedTime"])
            self.assertEqual({k: v for k, v in first.items() if k != "ProcessedTime"},
                             {k: v for k, v in second.items() if k != "ProcessedTime"})

    def test_every_click_points_to_an_impression(self):
        imps = {r["ImpressionId"]: r["CampaignId"] for r in self.rows["ttd_impressions"]}
        for r in self.rows["ttd_clicks"]:
            self.assertEqual(imps[r["DisplayImpressionId"]], r["CampaignId"])

    def test_meta_empty_spend_day(self):
        empty = [r for r in self.rows["meta_insights"] if r["spend"] == ""]
        self.assertEqual(len(empty), 3)                                  # 3 campaigns, one day
        self.assertEqual({r["date_start"] for r in empty}, {"2026-10-27"})
        self.assertEqual(self.manifest["traps"]["meta_empty_spend_date"], "2026-10-27")
        self.assertEqual(self.manifest["traps"]["meta_empty_spend_rows"], 3)
        for r in empty:
            self.assertEqual((r["impressions"], r["clicks"]), ("0", "0"))
        # every other Meta row has a real spend
        self.assertTrue(all(re.fullmatch(r"\d+\.\d\d", r["spend"])
                            for r in self.rows["meta_insights"] if r["spend"] != ""))

    def test_timezones(self):
        tz = self.manifest["timezones"]
        self.assertEqual(tz["meta_insights"], "Europe/Paris")
        self.assertEqual(tz["tiktok_integrated_report"], "Europe/Paris")
        for t in ("google_ads_campaign_stats", "dv360_impressions", "amazon_dsp_campaign",
                  "ttd_impressions", "ttd_clicks"):
            self.assertEqual(tz[t], "UTC", t)
        self.assertEqual(set(tz), set(generate.FIELDS))

    def test_dv360_currency_variants_differ(self):
        r = self.rows["dv360_impressions"][0]
        self.assertNotEqual(r["dv360_media_cost_usd"], r["dv360_media_cost_advertiser_currency"])
        self.assertNotEqual(r["dv360_media_cost_partner_currency"],
                            r["dv360_media_cost_advertiser_currency"])


class AmazonMap(unittest.TestCase):
    def test_map_equals_the_sources_table(self):
        m = json.loads((SYNTH / "amazon_v3_to_v1.json").read_text())
        m.pop("_source")
        self.assertEqual({k: v["v1"] for k, v in m.items()}, AMAZON_V1)
        # ROAS14d and dpv14d keep the note as written in SOURCES.md
        self.assertIn("metric.roasPromoted", m["ROAS14d"]["note"])
        self.assertIn("metric.detailPageViewsPromoted", m["dpv14d"]["note"])

    def test_map_equals_sources_file_when_present(self):
        if not SOURCES.exists():
            self.skipTest("schemas/SOURCES.md is not in the repo (gitignored)")
        text = SOURCES.read_text()
        block = text.split("Core CAMPAIGN fields", 1)[1].split("\n---", 1)[0]
        parsed = {}
        for line in block.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if line.startswith("|") and len(cells) == 4 and cells[0] not in ("Field (v3)", "---"):
                ids = None if cells[2].startswith("not seen") else re.findall(
                    r"(?<![\w.])((?:metric|campaign|adGroup|ad|date)\.\w+)", re.sub(r"\(.*?\)", "", cells[2]))
                parsed[cells[0]] = ids
        self.assertEqual(parsed, AMAZON_V1)

    def test_amazon_columns_are_v3_names(self):
        self.assertNotIn("metric.impressions", generate.COLUMNS["amazon_dsp_campaign"])
        self.assertTrue({"clickThroughs", "totalCost", "purchases14d", "sales14d"}
                        <= set(generate.COLUMNS["amazon_dsp_campaign"]))


class TruthHeaders(Generated):
    def recipe(self, name):
        return (ROOT / "recipes" / name).read_text()

    def test_recipe_14_header_matches_a_python_recompute(self):
        want = truth.parse_truth(self.recipe("14_cross_channel_daily_spend.sql"))
        self.assertEqual(want, truth.truth_14(self.dir))

    def test_recipe_15_header_matches_a_python_recompute(self):
        want = truth.parse_truth(self.recipe("15_ttd_click_dedup_reconciliation.sql"))
        self.assertEqual(want, truth.truth_15(self.dir))

    def test_truth_14_internal_consistency(self):
        t = truth.truth_14(self.dir)
        chans = [t[c] for c in truth.CHANNELS]
        self.assertEqual(sum(int(c["impressions"]) for c in chans), int(t["total"]["impressions"]))
        self.assertEqual(t["ttd"]["clicks"], str(self.manifest["traps"]["ttd_clicks_unique"]))
        self.assertEqual(t["dv360"]["clicks"], "null")
        self.assertEqual(t["meta_empty_day"]["spend_eur"], "0.00")


if __name__ == "__main__":
    unittest.main()
