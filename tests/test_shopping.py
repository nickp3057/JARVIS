import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
import brain.shopping as shopping
from brain.tools import execute_tool

_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class ShoppingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.patches = [patch.object(shopping, "_DB_PATH", root / "shopping.db"),
                        patch.object(shopping, "_PHOTO_DIR", root / "photos")]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.tempdir.cleanup()


class StoreTests(ShoppingTestCase):
    def test_suggest_matches_every_word_as_a_prefix(self) -> None:
        lotion = shopping.create_item("CeraVe PM facial lotion", note="Night, 3 oz", store="Walmart")
        shopping.create_item("Body lotion")
        names = [i["name"] for i in shopping.suggest("face lotion")]
        self.assertEqual(names, [])  # "face" is not a prefix of "facial"
        self.assertEqual([i["id"] for i in shopping.suggest("faci lot")], [lotion["id"]])
        self.assertEqual(len(shopping.suggest("lotion")), 2)
        self.assertEqual([i["id"] for i in shopping.suggest("walmart")], [lotion["id"]])

    def test_bought_leaves_the_list_but_stays_saved(self) -> None:
        item = shopping.create_item("Eggs", store="Walmart")
        self.assertEqual([i["id"] for i in shopping.current_list()], [item["id"]])
        bought = shopping.mark_bought(item["id"])
        self.assertFalse(bought["on_list"])
        self.assertEqual(bought["times_bought"], 1)
        self.assertEqual(shopping.current_list(), [])
        self.assertEqual(len(shopping.catalog()), 1)
        again = shopping.add_to_list(item["id"], 2)
        self.assertTrue(again["on_list"])
        self.assertEqual(again["quantity"], 2)

    def test_undo_bought_restores_and_uncounts(self) -> None:
        item = shopping.create_item("Milk")
        shopping.mark_bought(item["id"])
        restored = shopping.undo_bought(item["id"])
        self.assertTrue(restored["on_list"])
        self.assertEqual(restored["times_bought"], 0)

    def test_saved_without_listing(self) -> None:
        shopping.create_item("Sunscreen", on_list=False)
        self.assertEqual(shopping.current_list(), [])

    def test_photo_round_trip_and_delete_cleans_up(self) -> None:
        item = shopping.create_item("Toner")
        with self.assertRaises(ValueError):
            shopping.set_photo(item["id"], b"not a jpeg")
        updated = shopping.set_photo(item["id"], _JPEG)
        self.assertTrue(updated["has_photo"])
        self.assertEqual(updated["photo_version"], 1)
        self.assertTrue(shopping.photo_path(item["id"]).exists())
        self.assertTrue(shopping.delete_item(item["id"]))
        self.assertFalse(shopping.photo_path(item["id"]).exists())

    def test_png_cutout_replaces_jpeg_and_back(self) -> None:
        item = shopping.create_item("Lotion")
        shopping.set_photo(item["id"], _JPEG)
        updated = shopping.set_photo(item["id"], _PNG)
        self.assertEqual(updated["photo_version"], 2)
        path = shopping.photo_path(item["id"])
        self.assertEqual(path.suffix, ".png")
        self.assertEqual(shopping.photo_media_type(path), "image/png")
        self.assertFalse(path.with_suffix(".jpg").exists())
        shopping.set_photo(item["id"], _JPEG)
        self.assertEqual(shopping.photo_path(item["id"]).suffix, ".jpg")
        self.assertFalse(path.exists())
        shopping.set_photo(item["id"], _PNG)
        shopping.clear_photo(item["id"])
        self.assertFalse(shopping.photo_path(item["id"]).exists())

    def test_list_groups_by_store_with_unassigned_last(self) -> None:
        shopping.create_item("Batteries")
        shopping.create_item("Eggs", store="Walmart")
        shopping.create_item("Coffee", store="Costco")
        self.assertEqual([i["store"] for i in shopping.current_list()], ["Costco", "Walmart", ""])


class ToolTests(ShoppingTestCase):
    def test_add_reuses_a_saved_item(self) -> None:
        saved = shopping.create_item("CeraVe face lotion", on_list=False)
        reply = execute_tool("add_to_shopping_list", {"item": "face lotion"})
        self.assertIn("CeraVe face lotion", reply)
        self.assertTrue(shopping.get_item(saved["id"])["on_list"])
        self.assertEqual(len(shopping.catalog()), 1)

    def test_add_asks_when_several_match(self) -> None:
        shopping.create_item("Face lotion day", on_list=False)
        shopping.create_item("Face lotion night", on_list=False)
        reply = execute_tool("add_to_shopping_list", {"item": "face lotion"})
        self.assertIn("Several saved items", reply)
        self.assertEqual(shopping.current_list(), [])

    def test_add_new_and_tick_off(self) -> None:
        execute_tool("add_to_shopping_list", {"item": "Paper towels", "store": "Walmart"})
        self.assertIn("Walmart: Paper towels", execute_tool("shopping_list", {}))
        reply = execute_tool("mark_shopping_bought", {"items": ["paper towels", "caviar"]})
        self.assertIn("Ticked off Paper towels", reply)
        self.assertIn("Not on the list: caviar", reply)
        self.assertEqual(shopping.current_list(), [])


class RouteTests(ShoppingTestCase):
    def setUp(self) -> None:
        super().setUp()
        env = patch.dict("os.environ", {"JARVIS_API_TOKEN": "t"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(server.app)
        self.auth = {"Authorization": "Bearer t"}

    def test_create_photo_bought_flow(self) -> None:
        created = self.client.post("/api/shopping/items", headers=self.auth,
                                   json={"name": "Shampoo", "store": "Walmart"}).json()["item"]
        item_id = created["id"]
        photo = self.client.put(f"/api/shopping/items/{item_id}/photo", headers=self.auth, content=_JPEG)
        self.assertEqual(photo.status_code, 200)
        got = self.client.get(f"/api/shopping/items/{item_id}/photo", headers=self.auth)
        self.assertEqual(got.content, _JPEG)
        self.assertEqual(got.headers["content-type"], "image/jpeg")
        suggest = self.client.get("/api/shopping/suggest", params={"q": "sham"}, headers=self.auth).json()
        self.assertEqual([i["id"] for i in suggest["items"]], [item_id])
        self.client.post(f"/api/shopping/items/{item_id}/bought", headers=self.auth)
        overview = self.client.get("/api/shopping", headers=self.auth).json()
        self.assertEqual(overview["list"], [])
        self.assertEqual(overview["catalog"][0]["times_bought"], 1)
        relisted = self.client.post(f"/api/shopping/items/{item_id}/list", headers=self.auth, json={"quantity": 3})
        self.assertEqual(relisted.json()["item"]["quantity"], 3)

    def test_unknown_item_and_auth(self) -> None:
        self.assertEqual(self.client.post("/api/shopping/items/99/bought", headers=self.auth).status_code, 404)
        self.assertEqual(self.client.get("/api/shopping").status_code, 401)


if __name__ == "__main__":
    unittest.main()
