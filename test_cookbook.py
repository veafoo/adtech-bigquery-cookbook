#!/usr/bin/env python3
"""Tests for the cookbook.

1. Runner unit tests (no BigQuery): block parsing, fixture composition, dry-run parsing,
   byte guard.
2. Bug-proof tests on the frozen results (expected/*.json, produced by a real BigQuery run):
   each one pins the number that shows the naive pattern is wrong and the fix is right.
   The truths were computed by hand from the fixtures before the first run.
3. Live check (only with COOKBOOK_LIVE=1): re-runs every recipe and compares to expected/.

Run: python3 -m unittest test_cookbook -v
"""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_cookbook as rc
from synth import truth

EXP = Path(__file__).resolve().parent / "expected"


def rows(recipe, query):
    return json.loads((EXP / f"{recipe}.json").read_text())[query]["rows"]


def nbytes(recipe, query):
    return json.loads((EXP / f"{recipe}.json").read_text())[query]["bytes"]


def total(rs, col):
    return round(sum(float(r[col]) for r in rs), 2)


class RunnerUnit(unittest.TestCase):
    def test_split_blocks(self):
        text = "-- head\n-- @fixture\nt AS (SELECT 1 x)\n-- @query naive\nSELECT x FROM t\n-- @query fixed\nSELECT 2\n"
        fixture, queries = rc.split_blocks(text)
        self.assertEqual(fixture, "t AS (SELECT 1 x)")
        self.assertEqual(list(queries), ["naive", "fixed"])
        self.assertEqual(queries["naive"], "SELECT x FROM t")

    def test_split_blocks_rejects_duplicates_and_unnamed(self):
        with self.assertRaises(ValueError):
            rc.split_blocks("-- @query a\nSELECT 1\n-- @query a\nSELECT 2\n")
        with self.assertRaises(ValueError):
            rc.split_blocks("-- @query\nSELECT 1\n")

    def test_compose_merges_with_query_own_with(self):
        sql = rc.compose("t AS (SELECT 1 x)", "WITH q AS (SELECT x FROM t)\nSELECT * FROM q;")
        self.assertEqual(sql, "WITH t AS (SELECT 1 x),\nq AS (SELECT x FROM t)\nSELECT * FROM q")

    def test_compose_plain_select(self):
        self.assertEqual(rc.compose("t AS (SELECT 1 x)", "SELECT x FROM t"),
                         "WITH t AS (SELECT 1 x)\nSELECT x FROM t")

    def test_compose_drops_leading_comments_regression(self):
        # Regression (2026-11-01): a query starting with "-- comment" was passed to bq as an
        # argument starting with "--" and failed with "Unknown command line flag".
        sql = rc.compose(None, "-- reads the same leaf fields\nSELECT 1")
        self.assertEqual(sql, "SELECT 1")
        sql = rc.compose("t AS (SELECT 1 x)", "-- note\nWITH q AS (SELECT 1)\nSELECT 2")
        self.assertTrue(sql.startswith("WITH t AS"))

    def test_parse_dry_run(self):
        out = ("Query successfully validated. Assuming the tables are not modified, running "
               "this query will process 329097268 bytes of data.")
        self.assertEqual(rc.parse_dry_run(out), 329097268)
        with self.assertRaises(RuntimeError):
            rc.parse_dry_run("Error in query string")

    def test_byte_guard_refuses_big_scan(self):
        big = f"running this query will process {rc.MAX_BYTES + 1} bytes of data."
        with mock.patch.object(rc, "_bq", return_value=big) as bq:
            with self.assertRaises(RuntimeError):
                rc.run_query("SELECT 1")
            self.assertEqual(bq.call_count, 1)  # never ran the real query

    def test_every_recipe_parses_and_has_expected(self):
        for f in rc.recipe_files():
            _, queries = rc.split_blocks(f.read_text())
            self.assertTrue(queries, f.name)
            want = json.loads((EXP / f"{f.stem}.json").read_text())
            self.assertEqual(set(want), set(queries), f.name)


class SynthPlaceholder(unittest.TestCase):
    """Recipes on the synthetic data use @@SYNTH@@, filled from COOKBOOK_SYNTH_DATASET."""
    RECIPE = ("-- header\n-- @query q\nSELECT COUNT(*) AS n FROM `@@SYNTH@@.some_table`;\n")

    def run_main(self, env, argv):
        """Run the runner over a temp recipes dir holding one synth recipe and one plain one."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "recipes").mkdir()
            (tmp / "recipes" / "90_synth.sql").write_text(self.RECIPE)
            (tmp / "recipes" / "91_plain.sql").write_text("-- @query q\nSELECT 1 AS n;\n")
            seen = []

            def fake_run(sql):
                seen.append(sql)
                return {"bytes": 1, "rows": [{"n": "1"}]}

            clean = {k: v for k, v in os.environ.items() if k != rc.SYNTH_ENV}
            clean.update(env)
            out = io.StringIO()
            with mock.patch.dict(os.environ, clean, clear=True), \
                 mock.patch.object(rc, "RECIPES", tmp / "recipes"), \
                 mock.patch.object(rc, "EXPECTED", tmp / "expected"), \
                 mock.patch.object(rc, "run_query", fake_run), \
                 contextlib.redirect_stdout(out):
                code = rc.main(argv)
            frozen = {f.name for f in (tmp / "expected").glob("*.json")}
            return code, out.getvalue(), seen, frozen

    def test_unset_variable_skips_with_a_message_and_does_not_fail(self):
        code, out, seen, expected = self.run_main({}, ["--freeze"])
        self.assertEqual(code, 0)
        self.assertIn("SKIP 90_synth", out)
        self.assertIn(rc.SYNTH_ENV, out)
        self.assertEqual(seen, ["SELECT 1 AS n"])                   # only the plain recipe ran
        self.assertEqual(expected, {"91_plain.json"})                # nothing frozen for the skipped one

    def test_empty_variable_also_skips(self):
        code, out, seen, _ = self.run_main({rc.SYNTH_ENV: "  "}, ["--freeze"])
        self.assertEqual(code, 0)
        self.assertIn("SKIP 90_synth", out)

    def test_set_variable_fills_the_placeholder(self):
        code, out, seen, expected = self.run_main({rc.SYNTH_ENV: "my-proj.my_ds"}, ["--freeze"])
        self.assertEqual(code, 0)
        self.assertNotIn("SKIP", out)
        self.assertIn("SELECT COUNT(*) AS n FROM `my-proj.my_ds.some_table`", seen)
        self.assertEqual(expected, {"90_synth.json", "91_plain.json"})

    def test_malformed_dataset_is_refused_not_injected(self):
        with mock.patch.dict(os.environ, {rc.SYNTH_ENV: "p.d` ; DROP TABLE x"}):
            with self.assertRaises(ValueError):
                rc.synth_dataset()

    def test_only_takes_a_comma_list(self):
        names = [f.name[:2] for f in rc.recipe_files("14,15")]
        self.assertEqual(names, ["14", "15"])
        self.assertEqual(len(rc.recipe_files("01")), 1)

    def test_committed_synth_recipes_hardcode_no_project(self):
        # every table reference in recipes 14 and 15 goes through the placeholder
        for f in rc.recipe_files("14,15"):
            text = f.read_text()
            refs = re.findall(r"`([^`]+)`", text)
            self.assertTrue(refs, f.name)
            for ref in refs:
                self.assertTrue(ref.startswith(rc.SYNTH_PLACEHOLDER + "."), f"{f.name}: {ref}")


class BugProofs(unittest.TestCase):
    def test_01_dedup(self):
        self.assertEqual(total(rows("01_dedup", "naive"), "conversion_value"), 200.0)
        self.assertEqual(total(rows("01_dedup", "fixed"), "conversion_value"), 140.0)
        naive_clicks = {r["click_id"] for r in rows("01_dedup", "naive")}
        self.assertNotIn("c5", naive_clicks)  # second purchase without ID silently lost
        self.assertIn("c1", naive_clicks)     # T1 counted again after midnight

    def test_02_offline_conversions(self):
        naive = rows("02_offline_conversions_vbb", "naive")
        self.assertEqual(len(naive), 4)  # 2 leads x 2 sales fan-out
        self.assertIn("-40.0", [r["conversion_value"] for r in naive])
        fixed = rows("02_offline_conversions_vbb", "fixed")
        self.assertEqual(fixed, [{"ad_user_data": "GRANTED", "conversion_date_time": "2021-01-31 09:00:00+01:00",
                                  "conversion_value": "80.0", "currency_code": "EUR", "gbraid": None,
                                  "gclid": None, "order_id": "S1", "wbraid": "w1"}])

    def test_03_clean_room_fanout(self):
        self.assertEqual(rows("03_clean_room_fanout", "naive"),
                         [{"campaign_id": "C1", "total_revenue": "350.0", "unique_converters": "2"}])
        fixed = {r["campaign_id"]: r["influenced_revenue"] for r in rows("03_clean_room_fanout", "fixed")}
        self.assertEqual(fixed, {"C1": "150.0", "C2": "70.0"})

    def test_04_json_consent(self):
        naive = rows("04_json_consent_parsing", "naive")
        self.assertTrue(all(r["dma_status"] == "Missing Consent" and r["gcd_string"] is None for r in naive))
        fixed = rows("04_json_consent_parsing", "fixed")
        self.assertEqual((fixed[0]["wbraid"], fixed[0]["coupon"]), ("WB123", "WINTER"))
        self.assertTrue(all(r["gcd_string"] for r in fixed))

    def test_05a_cross_join_does_not_change_bytes(self):
        scalar, cross = "scalar_subqueries", "cross_join_unnest"
        self.assertEqual(nbytes("05a_ga4_unnest_cost", scalar), nbytes("05a_ga4_unnest_cost", cross))
        self.assertGreater(int(rows("05a_ga4_unnest_cost", cross)[0]["output_rows"]),
                           10 * int(rows("05a_ga4_unnest_cost", scalar)[0]["output_rows"]))
        self.assertEqual(rows("05a_ga4_unnest_cost", scalar)[0]["rows_with_gclid"], "0")

    def test_05b_gclid(self):
        self.assertTrue(all(r["gclid"] is None for r in rows("05b_ga4_gclid_on_conversions", "naive")))
        fixed = {r["user_pseudo_id"]: r["session_gclid"] for r in rows("05b_ga4_gclid_on_conversions", "fixed")}
        self.assertEqual(fixed, {"u1": "GCLID-A", "u2": None})  # same ga_session_id, no leak to u2

    def test_06_linear_attribution(self):
        self.assertEqual(total(rows("06_linear_attribution", "naive"), "linear_attributed_revenue"), 187.5)
        fixed = rows("06_linear_attribution", "fixed")
        self.assertAlmostEqual(total(fixed, "linear_attributed_revenue"), 300.0, delta=0.02)
        self.assertNotIn("C0", [r["campaign_name"] for r in fixed])

    def test_07_item_funnel_fanout(self):
        fan = rows("07_item_funnel", "fanout")[0]
        self.assertGreater(int(fan["joined_rows"]), int(fan["add_rows"]))  # the join multiplies rows
        self.assertGreater(int(fan["max_rows_one_user_item"]), 1)
        naive = rows("07_item_funnel", "naive")[0]
        fixed = rows("07_item_funnel", "fixed")[0]
        # Regression of an earlier written review that said "the rate goes negative": impossible
        # with a LEFT JOIN (COUNT(p) <= COUNT(a)), and measured 0 on the real data.
        self.assertEqual(naive["negative_rates"], "0")
        self.assertLess(float(naive["min_rate"]), float(fixed["min_rate"]))  # pushed toward 0

    def test_08_null_user_sessions(self):
        naive = rows("08_lag_sessionisation", "naive")
        self.assertEqual(sum(r["custom_session_id"] is None for r in naive), 2)
        fixed = [r["custom_session_id"] for r in rows("08_lag_sessionisation", "fixed")]
        self.assertEqual(fixed, ["u1_1", "u1_1", "u1_2", "u2_1"])

    def test_08b_session_id_alone_is_not_a_key(self):
        r = rows("08b_sessionisation_vs_ga4", "compare")[0]
        self.assertLess(int(r["ga4_session_ids_alone"]), int(r["ga4_sessions_user_and_id"]))

    def test_09_lead_funnel(self):
        shared = rows("09_lead_funnel", "shared_session_ids")[0]
        self.assertGreater(int(shared["shared_by_several_users"]), 0)
        self.assertGreater(int(rows("09_lead_funnel", "naive")[0]["pairs"]),
                           int(rows("09_lead_funnel", "fixed")[0]["pairs"]))

    def test_10_stitching(self):
        naive = [r["stitched_pseudo_id"] for r in rows("10_identity_stitching", "naive")]
        self.assertEqual(naive[:3], ["B", "B", "B"])  # visitor A overwritten by B
        fixed = [r["stitched_pseudo_id"] for r in rows("10_identity_stitching", "fixed")]
        self.assertEqual(fixed, ["A", "A", "A", None, "B", "B"])

    def test_11_outage_alert(self):
        naive = {r["event_date"]: r["tracking_status"] for r in rows("11_tracking_health_check", "naive")}
        self.assertNotIn("2021-01-27", naive)               # the outage day has no row
        self.assertTrue(naive["2021-01-31"].startswith("CRITICAL"))  # partial day false alarm
        fixed = {r["event_date"]: r["tracking_status"] for r in rows("11_tracking_health_check", "fixed")}
        self.assertEqual([d for d, s in fixed.items() if s != "HEALTHY"], ["2021-01-27"])

    def test_12_hll(self):
        self.assertEqual(nbytes("12_hll_unique_users", "hll"), nbytes("12_hll_unique_users", "exact"))
        exact = {(r["year"], r["month"]): int(r["exact_unique_users"]) for r in rows("12_hll_unique_users", "exact")}
        for r in rows("12_hll_unique_users", "hll"):
            e = exact[(r["year"], r["month"])]
            self.assertLess(abs(int(r["approx_unique_users"]) - e) / e, 0.0115)

    def test_13_ltv_censoring(self):
        naive = rows("13_ltv_features", "naive")[0]
        fixed = rows("13_ltv_features", "fixed")[0]
        self.assertLess(int(fixed["users"]), int(naive["users"]) / 10)
        self.assertGreater(float(fixed["mean_label"]), float(naive["mean_label"]))

    def test_14_cross_channel_daily_spend(self):
        recipe = (Path(rc.RECIPES) / "14_cross_channel_daily_spend.sql").read_text()
        want = truth.parse_truth(recipe)
        fixed, naive = rows("14_cross_channel_daily_spend", "fixed"), rows("14_cross_channel_daily_spend", "naive")
        per = {}
        for r in fixed:
            t = per.setdefault(r["channel"], [0, 0, 0, False])
            t[0] += round(float(r["spend_eur"]), 2)
            t[1] += int(r["impressions"])
            if r["clicks"] is not None:
                t[2] += int(r["clicks"])
                t[3] = True
        for ch in truth.CHANNELS:
            self.assertAlmostEqual(per[ch][0], float(want[ch]["spend_eur"]), delta=0.005, msg=ch)
            self.assertEqual(str(per[ch][1]), want[ch]["impressions"], ch)
            self.assertEqual(str(per[ch][2]) if per[ch][3] else "null", want[ch]["clicks"], ch)
        self.assertEqual(len(fixed), 28 * 6)                         # no NULL day, no missing row
        self.assertTrue(all(r["date"] for r in fixed))
        # Meta's empty-string day: 0 in the fixed table, NULL in the naive one
        day = want["meta_empty_day"]["date"]
        pick = lambda rs: [r for r in rs if r["date"] == day and r["channel"] == "meta"][0]
        self.assertEqual(float(pick(fixed)["spend_eur"]), 0.0)
        self.assertIsNone(pick(naive)["spend_eur"])
        # naive: micros and nanos summed as EUR, TikTok on a NULL day, duplicated TTD clicks
        gtot = sum(float(r["spend_eur"]) for r in naive if r["channel"] == "google_ads")
        self.assertGreater(gtot, 1000 * float(want["google_ads"]["spend_eur"]))
        self.assertTrue(any(r["date"] is None and r["channel"] == "tiktok" for r in naive))
        ttd_clicks = sum(int(r["clicks"]) for r in naive if r["channel"] == "ttd")
        self.assertEqual(ttd_clicks, int(want["ttd"]["clicks"]) + 20)
        self.assertGreater(nbytes("14_cross_channel_daily_spend", "fixed"), 0)

    def test_15_ttd_click_dedup(self):
        recipe = (Path(rc.RECIPES) / "15_ttd_click_dedup_reconciliation.sql").read_text()
        want = truth.parse_truth(recipe)
        naive = {r["campaign_id"]: r for r in rows("15_ttd_click_dedup_reconciliation", "naive")}
        fixed = {r["campaign_id"]: r for r in rows("15_ttd_click_dedup_reconciliation", "fixed")}
        for c in ("ttdcmp1", "ttdcmp2", "ttdcmp3"):
            self.assertEqual(naive[c]["clicks"], want[c]["clicks_raw"])
            self.assertEqual(fixed[c]["clicks"], want[c]["clicks_dedup"])
            self.assertEqual(fixed[c]["impressions"], want[c]["impressions"])
            self.assertAlmostEqual(float(naive[c]["ctr"]), float(want[c]["ctr_raw"]), places=4)
            self.assertAlmostEqual(float(fixed[c]["ctr"]), float(want[c]["ctr_dedup"]), places=4)
            self.assertGreater(float(naive[c]["ctr"]), float(fixed[c]["ctr"]) - 1e-9)
        check = rows("15_ttd_click_dedup_reconciliation", "dup_check")[0]
        self.assertEqual(check["duplicate_rows"], want["duplicate_rows"]["rows"])


@unittest.skipUnless(os.environ.get("COOKBOOK_LIVE") == "1", "set COOKBOOK_LIVE=1 to query BigQuery")
class Live(unittest.TestCase):
    def test_all_recipes_match_frozen_results(self):
        self.assertEqual(rc.main([]), 0)


if __name__ == "__main__":
    unittest.main()
