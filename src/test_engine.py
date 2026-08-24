"""
Unit Tests for the FoodPlanner matching and shopping-list engine.

Covers the logic that decides what actually lands on the shopping list:
- Deal matching (trap lists, processed-product filter, fuzzy fallback)
- Price plausibility
- Portion -> quantity rules
- Pantry deduction and pack-count optimisation
- The deals summary handed to Gemini

These are the paths where a silent error produces a wrong list, which is
why they are tested against the real config/meal_templates.json.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (
    build_deals_summary,
    calculate_quantity,
    clean_currency,
    find_cheapest_deal,
    find_matching_deals,
    generate_shopping_list,
    is_match,
    is_price_plausible,
    parse_scraped_text,
)

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


def deal(item, price, store="Netto", unit_size=1.0, unit_type="stk"):
    """Builds a deal dict in the shape parse_scraped_text emits."""
    return {
        "item": item,
        "price": price,
        "store": store,
        "unit_size": unit_size,
        "unit_type": unit_type,
        "raw": f"{item} {price}",
    }


class TestTemplateIngredientsAreMatchable(unittest.TestCase):
    """
    Regression guard for the trap-list collisions that silently disabled three
    ingredients: kokosmaelk inherited maelk's trap list (which lists kokosmaelk),
    risnudler inherited is's list (which lists ris), and poelser was filtered by
    the 'poelse' processed-product marker.
    """

    def setUp(self):
        with open(os.path.join(CONFIG_DIR, "meal_templates.json"), encoding="utf-8") as f:
            self.ingredients = sorted({i for t in json.load(f) for i in t["ingredients"]})

    def test_every_template_ingredient_can_match_a_deal_named_after_it(self):
        dead = [i for i in self.ingredients if not is_match(i, f"{i} 500 g")]
        self.assertEqual(dead, [], f"These ingredients can never match any deal: {dead}")

    def test_templates_are_not_empty(self):
        # generate_weekly_plan divides by len(ingredients); an empty list would raise.
        self.assertGreater(len(self.ingredients), 0)


class TestIsMatch(unittest.TestCase):
    """The trap lists must still reject what they were written to reject."""

    def test_rejects_processed_chicken(self):
        self.assertFalse(is_match("Kylling", "Kyllingenuggets 500g"))
        self.assertFalse(is_match("Kylling", "Kylling Cordon Bleu"))

    def test_accepts_raw_chicken(self):
        self.assertTrue(is_match("Kylling", "Hel Kylling 1200g"))
        self.assertTrue(is_match("Kylling", "Kyllingebryst 700g"))

    def test_milk_still_rejects_substitutes(self):
        # The fix must not make "maelk" match every milk-like product.
        self.assertFalse(is_match("Mælk", "Kokosmælk 400 ml"))
        self.assertFalse(is_match("Mælk", "Kakaomælk 1L"))
        self.assertTrue(is_match("Mælk", "Letmælk 1L"))

    def test_coconut_milk_matches_itself_but_not_plain_milk(self):
        self.assertTrue(is_match("Kokosmælk", "Kokosmælk 400 ml"))
        self.assertFalse(is_match("Kokosmælk", "Letmælk 1L"))

    def test_rice_variants(self):
        self.assertTrue(is_match("Ris", "Jasminris 1 kg"))
        self.assertFalse(is_match("Ris", "Risengrød 1kg"))
        self.assertTrue(is_match("Risnudler", "Risnudler 250g"))

    def test_sausages_match_despite_processed_marker(self):
        self.assertTrue(is_match("Pølser", "Wienerpølser 400g"))
        self.assertTrue(is_match("Pølser", "Grillpølser 8 stk"))

    def test_beef_rejects_ready_meals(self):
        self.assertTrue(is_match("Oksekød", "Hakket Oksekød 4-7% 500g"))
        self.assertFalse(is_match("Oksekød", "Oksekødslasagne 400g"))

    def test_multiword_requires_all_words(self):
        self.assertTrue(is_match("Hakket oksekød", "Hakket Oksekød 500g"))
        self.assertFalse(is_match("Hakket oksekød", "Oksekød i skiver"))

    def test_short_word_is_does_not_match_everything(self):
        # "is" (ice cream) must not match "ris", "frisk", "chips", ...
        self.assertFalse(is_match("Is", "Basmati ris 1kg"))
        self.assertFalse(is_match("Is", "Frisk spinat"))


class TestShortTermDanishCollisions(unittest.TestCase):
    """
    Short ingredient names collide with unrelated Danish words that contain or
    end in them. Every case here was a real false positive observed against a
    live scrape of REMA/Netto/365/Lidl.
    """

    def test_ris_does_not_match_price_or_pork_or_sweets(self):
        self.assertFalse(is_match("Ris", "AMA madlavning App-pris"))   # pris = price
        self.assertFalse(is_match("Ris", "Literpris ."))               # appears on every line
        self.assertFalse(is_match("Ris", "Frilandsgris Bag-selv"))     # gris = pig
        self.assertFalse(is_match("Ris", "Sønderjyske Fristelser"))    # sweets

    def test_ris_still_matches_actual_rice(self):
        self.assertTrue(is_match("Ris", "Ris 500g"))
        self.assertTrue(is_match("Ris", "Jasminris 1 kg"))

    def test_aeg_does_not_match_cattle(self):
        self.assertFalse(is_match("Æg", "Hakket dansk oksekød ungkvæg"))  # kvæg = cattle

    def test_aeg_still_matches_compound_egg_products(self):
        self.assertTrue(is_match("Æg", "Dava skrabeæg"))
        self.assertTrue(is_match("Æg", "Æg 10 stk"))

    def test_ost_does_not_match_juice_or_frost(self):
        self.assertFalse(is_match("Ost", "Ørskov Frugt Æblemost"))  # most = juice
        self.assertFalse(is_match("Ost", "Frost varer"))

    def test_ost_still_matches_compound_cheeses(self):
        self.assertTrue(is_match("Ost", "Buko pisket flødeost"))
        self.assertTrue(is_match("Ost", "Mammen skæreost"))
        self.assertTrue(is_match("Ost", "Riberhus ost"))


class TestPricePlausibility(unittest.TestCase):
    def test_rejects_implausibly_cheap_chicken_per_kg(self):
        # 5 kr for 1kg of chicken is not raw chicken.
        self.assertFalse(is_price_plausible("kylling", deal("Kylling", 5.0, unit_size=1000, unit_type="g")))

    def test_accepts_normal_chicken_price(self):
        self.assertTrue(is_price_plausible("kylling", deal("Kylling", 60.0, unit_size=1000, unit_type="g")))

    def test_unknown_ingredient_passes(self):
        self.assertTrue(is_price_plausible("tacoskaller", deal("Tacoskaller", 15.0)))


class TestCalculateQuantity(unittest.TestCase):
    def test_scalable_items_multiply_by_portions(self):
        self.assertEqual(calculate_quantity("kylling", 4), (600, "g"))
        self.assertEqual(calculate_quantity("æg", 4), (8, "stk"))
        self.assertEqual(calculate_quantity("kartofler", 4), (1000, "g"))

    def test_pack_items_ignore_portions(self):
        self.assertEqual(calculate_quantity("olivenolie", 4), (1, "stk"))
        self.assertEqual(calculate_quantity("olivenolie", 12), (1, "stk"))

    def test_whole_chicken_is_a_pack_not_a_weight(self):
        # Direct lookup must win over the "kylling" substring rule.
        self.assertEqual(calculate_quantity("hel kylling", 4), (1, "stk"))

    def test_unknown_ingredient_defaults_to_one_pack(self):
        self.assertEqual(calculate_quantity("noget helt ukendt", 4), (1, "stk"))


class TestCleanCurrency(unittest.TestCase):
    def test_danish_formats(self):
        self.assertEqual(clean_currency("12,95 kr"), 12.95)
        self.assertEqual(clean_currency("15.-"), 15.0)
        self.assertEqual(clean_currency("20 DKK"), 20.0)

    def test_unparseable_returns_none(self):
        self.assertIsNone(clean_currency("tilbud"))
        self.assertIsNone(clean_currency(""))
        self.assertIsNone(clean_currency(None))


class TestParseScrapedText(unittest.TestCase):
    def test_extracts_price_and_unit(self):
        deals = parse_scraped_text("Kyllingebryst 700 g 45,00 kr\nJunk line\n", "Netto")
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0]["price"], 45.00)
        self.assertEqual(deals[0]["unit_size"], 700.0)
        self.assertEqual(deals[0]["unit_type"], "g")

    def test_kilograms_normalised_to_grams(self):
        deals = parse_scraped_text("Kartofler 2 kg 25,00 kr", "Netto")
        self.assertEqual(deals[0]["unit_size"], 2000.0)
        self.assertEqual(deals[0]["unit_type"], "g")

    def test_lines_without_price_are_skipped(self):
        self.assertEqual(parse_scraped_text("Ugens tilbud\nBare tekst\n", "Netto"), [])


class TestFindDeals(unittest.TestCase):
    def setUp(self):
        self.deals = [
            deal("Kyllingebryst", 60.0, "Netto", 1000, "g"),
            deal("Kyllingebryst", 45.0, "Lidl", 1000, "g"),
            deal("Kyllingenuggets", 20.0, "Rema", 500, "g"),
        ]

    def test_returns_cheapest_valid_match(self):
        best = find_cheapest_deal("Kylling", self.deals)
        self.assertEqual(best["price"], 45.0)
        self.assertEqual(best["store"], "Lidl")

    def test_excludes_processed_products_from_matches(self):
        items = [d["item"] for d in find_matching_deals("Kylling", self.deals)]
        self.assertNotIn("Kyllingenuggets", items)

    def test_no_match_returns_none(self):
        self.assertIsNone(find_cheapest_deal("Ananas", self.deals))


class TestBuildDealsSummary(unittest.TestCase):
    def test_includes_prices_and_groups_by_store(self):
        summary = build_deals_summary([deal("Kylling", 45.0, "Lidl"), deal("Laks", 60.0, "Netto")])
        self.assertIn("--- Lidl ---", summary)
        self.assertIn("--- Netto ---", summary)
        self.assertIn("45.00 kr", summary)

    def test_deduplicates_repeated_offers(self):
        dupes = [deal("Kylling", 45.0, "Lidl")] * 5
        self.assertEqual(build_deals_summary(dupes).count("Kylling"), 1)

    def test_respects_the_cap_and_keeps_cheapest(self):
        many = [deal(f"Vare {i}", float(100 - i), "Lidl") for i in range(50)]
        summary = build_deals_summary(many, max_deals=3)
        # Cap applies to deal lines, not the store header.
        self.assertEqual(len([l for l in summary.split("\n") if " kr" in l]), 3)
        self.assertIn("Vare 49", summary)  # cheapest at 51.00

    def test_empty_input(self):
        self.assertEqual(build_deals_summary([]), "No deals available.")


class TestGenerateShoppingList(unittest.TestCase):
    def setUp(self):
        self.schedule = [
            {"day_name": "Monday", "type": "cook", "meal_name": "Kyllingegryde",
             "portions": 4, "ingredients": ["Kylling", "Ris"]},
            {"day_name": "Tuesday", "type": "leftover", "meal_name": "Rester", "portions": 0},
        ]
        self.deals = [deal("Kyllingebryst", 45.0, "Lidl", 1000, "g")]

    def test_leftover_days_add_nothing(self):
        _, flat, _ = generate_shopping_list([], self.schedule, self.deals, [])
        names = {e["name"] for e in flat}
        self.assertEqual(names, {"Kylling", "Ris"})

    def test_matched_ingredient_carries_price_and_store(self):
        _, flat, _ = generate_shopping_list([], self.schedule, self.deals, [])
        chicken = next(e for e in flat if e["name"] == "Kylling")
        self.assertEqual(chicken["store"], "Lidl")
        self.assertEqual(chicken["found_name"], "Kyllingebryst")

    def test_unmatched_ingredient_falls_back_to_general(self):
        _, flat, _ = generate_shopping_list([], self.schedule, self.deals, [])
        rice = next(e for e in flat if e["name"] == "Ris")
        self.assertEqual(rice["store"], "General/Other")
        self.assertEqual(rice["price"], 0.0)

    def test_pack_count_rounds_up_to_cover_the_need(self):
        # 4 portions of chicken = 600 g; a 500 g pack means buying 2.
        small_pack = [deal("Kyllingebryst", 30.0, "Lidl", 500, "g")]
        _, flat, _ = generate_shopping_list([], self.schedule, small_pack, [])
        chicken = next(e for e in flat if e["name"] == "Kylling")
        self.assertEqual(chicken["buy_qty"], 2)
        self.assertEqual(chicken["price"], 60.0)

    def test_buying_list_items_are_included(self):
        _, flat, _ = generate_shopping_list(["Kaffe"], self.schedule, self.deals, [])
        self.assertIn("Kaffe", {e["name"] for e in flat})

    def test_pantry_quantity_is_deducted_from_the_need(self):
        # Need 8 eggs for 4 portions; pantry has 6, so only 2 remain.
        schedule = [{"day_name": "Monday", "type": "cook", "meal_name": "Omelet",
                     "portions": 4, "ingredients": ["Æg"]}]
        _, flat, _ = generate_shopping_list([], schedule, [], ["Æg 6 stk"])
        eggs = next(e for e in flat if e["name"] == "Æg")
        self.assertEqual(eggs["total_needed"], "2.0 stk")

    def test_fully_stocked_ingredient_leaves_the_list(self):
        # The pantry covers it, so there is nothing to buy - it must not appear
        # at all, rather than showing up as "0.0 stk" against a deal.
        schedule = [{"day_name": "Monday", "type": "cook", "meal_name": "Omelet",
                     "portions": 4, "ingredients": ["Æg"]}]
        _, flat, _ = generate_shopping_list([], schedule, [], ["Æg 99 stk"])
        self.assertEqual([e for e in flat if e["name"] == "Æg"], [])

    def test_partially_stocked_ingredient_stays_with_the_remainder(self):
        schedule = [{"day_name": "Monday", "type": "cook", "meal_name": "Omelet",
                     "portions": 4, "ingredients": ["Æg"]}]
        _, flat, _ = generate_shopping_list([], schedule, [], ["Æg 6 stk"])
        eggs = next(e for e in flat if e["name"] == "Æg")
        self.assertEqual(eggs["total_needed"], "2.0 stk")

    def test_savings_measures_the_real_spread_not_a_flat_percentage(self):
        # Cheapest 45 vs dearest 60 for the same ingredient = 15 kr saved.
        spread = [
            deal("Kyllingebryst", 60.0, "Netto", 1000, "g"),
            deal("Kyllingebryst", 45.0, "Lidl", 1000, "g"),
        ]
        _, _, savings = generate_shopping_list([], self.schedule, spread, [])
        self.assertAlmostEqual(savings, 15.0)

    def test_no_savings_claimed_when_only_one_offer_exists(self):
        _, _, savings = generate_shopping_list([], self.schedule, self.deals, [])
        self.assertEqual(savings, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
