"""
Unit tests for the SQLite store.

Each test runs against a throwaway database file so the real
data/deals_cache.db is never touched.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._real_db = store.DB_FILE
        store.DB_FILE = self.path
        store.init_db()

    def tearDown(self):
        store.DB_FILE = self._real_db
        try:
            os.unlink(self.path)
        except OSError:
            pass


class TestPantry(StoreTestCase):
    def test_add_and_read_back(self):
        store.upsert_pantry_item("Æg", 6, "stk")
        items = store.get_pantry()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Æg")
        self.assertEqual(items[0]["qty"], 6)

    def test_upsert_replaces_rather_than_duplicating(self):
        store.upsert_pantry_item("Æg", 6, "stk")
        store.upsert_pantry_item("Æg", 2, "stk")
        items = store.get_pantry()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["qty"], 2)

    def test_blank_name_rejected(self):
        with self.assertRaises(ValueError):
            store.upsert_pantry_item("   ")

    def test_delete(self):
        store.upsert_pantry_item("Æg", 6, "stk")
        store.delete_pantry_item(store.get_pantry()[0]["id"])
        self.assertEqual(store.get_pantry(), [])

    def test_lines_render_in_the_format_the_engine_parses(self):
        store.upsert_pantry_item("Æg", 6, "stk")
        store.upsert_pantry_item("Olivenolie", None, None)
        lines = store.pantry_as_lines()
        self.assertIn("Æg 6 stk", lines)
        self.assertIn("Olivenolie", lines)

    def test_whole_numbers_do_not_render_as_floats(self):
        # "Æg 6.0 stk" would still parse, but it reads like a bug in the UI.
        store.upsert_pantry_item("Æg", 6.0, "stk")
        self.assertEqual(store.pantry_as_lines(), ["Æg 6 stk"])


class TestBuyingList(StoreTestCase):
    def test_add_and_toggle(self):
        store.add_buying_item("Kaffe")
        item = store.get_buying_list()[0]
        self.assertEqual(item["done"], 0)
        store.toggle_buying_item(item["id"])
        self.assertEqual(store.get_buying_list()[0]["done"], 1)

    def test_duplicates_are_ignored(self):
        store.add_buying_item("Kaffe")
        store.add_buying_item("Kaffe")
        self.assertEqual(len(store.get_buying_list()), 1)

    def test_done_items_are_excluded_from_the_engine_feed(self):
        store.add_buying_item("Kaffe")
        store.add_buying_item("Mel")
        store.toggle_buying_item(store.get_buying_list()[0]["id"])
        self.assertEqual(len(store.buying_as_lines()), 1)


class TestPlans(StoreTestCase):
    def test_round_trips_schedule_and_shopping(self):
        schedule = [{"day_name": "Mandag", "type": "cook", "meal_name": "Wok",
                     "portions": 4, "ingredients": ["Kylling"]}]
        shopping = [{"name": "Kylling", "price": 29.0, "buy_qty": 1,
                     "found_name": "DANPO", "store": "Lidl", "total_needed": "600.0 g"}]
        store.save_plan(schedule, shopping, 17.0, "gemini")

        plan = store.get_latest_plan()
        self.assertEqual(plan["schedule"], schedule)
        self.assertEqual(plan["shopping"], shopping)
        self.assertEqual(plan["savings"], 17.0)
        self.assertEqual(plan["source"], "gemini")

    def test_latest_plan_is_the_newest(self):
        store.save_plan([], [], 1.0, "rule-based")
        store.save_plan([], [], 2.0, "gemini")
        self.assertEqual(store.get_latest_plan()["savings"], 2.0)

    def test_latest_plan_breaks_created_at_ties_by_insertion_order(self):
        # time.time() is ~15ms granular on Windows, so back-to-back saves can
        # share a created_at. Insertion order must still decide which is latest.
        now = 1700000000.0
        with store.connect() as conn:
            for savings in (1.0, 2.0, 3.0):
                conn.execute(
                    "INSERT INTO plans (created_at, schedule, shopping, savings, source) "
                    "VALUES (?, '[]', '[]', ?, 'test')", (now, savings))
        self.assertEqual(store.get_latest_plan()["savings"], 3.0)
        self.assertEqual([p["savings"] for p in store.list_plans()], [3.0, 2.0, 1.0])

    def test_no_plans_yet(self):
        self.assertIsNone(store.get_latest_plan())

    def test_danish_characters_survive_the_json_round_trip(self):
        store.save_plan([{"meal_name": "Æggekage med rødbeder"}], [], 0.0)
        self.assertEqual(store.get_latest_plan()["schedule"][0]["meal_name"],
                         "Æggekage med rødbeder")


class TestRuns(StoreTestCase):
    def test_start_and_finish(self):
        run_id = store.start_run()
        self.assertEqual(store.list_runs()[0]["status"], "running")
        store.finish_run(run_id, "ok", 343, "gemini plan")
        run = store.list_runs()[0]
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["deals_found"], 343)
        self.assertIsNotNone(run["duration"])


class TestParsePantryLine(unittest.TestCase):
    """Pure function - no database needed."""

    def test_name_qty_unit(self):
        self.assertEqual(store.parse_pantry_line("Æg 6 stk"), ("Æg", 6.0, "stk"))

    def test_defaults_unit_when_absent(self):
        self.assertEqual(store.parse_pantry_line("Æg 6"), ("Æg", 6.0, "stk"))

    def test_decimal_comma(self):
        self.assertEqual(store.parse_pantry_line("Mel 1,5 kg"), ("Mel", 1.5, "kg"))

    def test_no_quantity_leaves_it_unknown(self):
        self.assertEqual(store.parse_pantry_line("Olivenolie"), ("Olivenolie", None, None))

    def test_empty(self):
        self.assertEqual(store.parse_pantry_line(""), (None, None, None))
        self.assertEqual(store.parse_pantry_line(None), (None, None, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
