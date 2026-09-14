"""Tests for guarded, durable public-page price watching."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import price_watch


class PriceWatchTests(unittest.TestCase):
    def test_extracts_json_ld_offer(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type":"Product","offers":{"price":"249.99","priceCurrency":"USD"}}
        </script>
        """
        self.assertEqual(price_watch.extract_price(html), (249.99, "USD"))

    def test_extracts_standard_product_meta(self) -> None:
        html = '<meta property="product:price:amount" content="1,299.00">' \
               '<meta property="product:price:currency" content="USD">'
        self.assertEqual(price_watch.extract_price(html), (1299.0, "USD"))

    def test_rejects_local_urls_without_requesting_them(self) -> None:
        with self.assertRaisesRegex(ValueError, "Local"):
            price_watch._validate_public_url("http://localhost:8080/admin")
        with self.assertRaisesRegex(ValueError, "Local"):
            price_watch._validate_public_url("http://printer.local/status")

    def test_persists_and_alerts_when_target_is_reached(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder) / "watches.json"
            with patch.object(price_watch, "_WATCHES_FILE", store), \
                 patch.object(price_watch, "_validate_public_url"), \
                 patch.object(price_watch, "_fetch_price", return_value=(120.0, "USD")):
                item = price_watch.add_price_watch(
                    "https://shop.example/product", "Headphones", target_price=100
                )
                self.assertTrue(store.exists())
                self.assertEqual(price_watch.price_watches()[0].id, item.id)
            with patch.object(price_watch, "_WATCHES_FILE", store), \
                 patch.object(price_watch, "_fetch_price", side_effect=[(99.0, "USD"), (98.0, "USD")]):
                notices = price_watch.check_price_watches()
                repeated = price_watch.check_price_watches()
            self.assertEqual(notices[0]["reason"], "target")
            self.assertEqual(repeated, [])


if __name__ == "__main__":
    unittest.main()
