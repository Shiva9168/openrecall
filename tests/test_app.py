"""Unit tests for Phase 6A Flask routes, REST API endpoints, and template rendering."""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from openrecall.app import app
from openrecall.database import create_db, insert_entry


class TestAppRoutesPhase6A(unittest.TestCase):
    """Test suite for Phase 6A Flask web interface and JSON REST APIs."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_recall.db")
        create_db(self.db_path)

        app.config["TESTING"] = True
        self.client = app.test_client()

        # Seed sample data
        self.now = int(time.time())
        with patch("openrecall.database.db_path", self.db_path):
            insert_entry(
                text="Flask timeline backend test entry text. Tesseract OCR active.",
                timestamp=self.now,
                app="Firefox",
                title="OpenRecall GitHub Repository",
                image_path=f"{self.now}_0.webp",
                target_path=self.db_path,
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_timeline_route_html(self):
        with patch("openrecall.database.db_path", self.db_path):
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn("Timeline History", html)
            self.assertIn("Firefox", html)
            self.assertIn("OpenRecall GitHub Repository", html)

    def test_search_route_html(self):
        with patch("openrecall.database.db_path", self.db_path):
            response = self.client.get("/search?q=Tesseract")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn("Search Results for", html)
            self.assertIn("Firefox", html)

    def test_api_timeline_json(self):
        with patch("openrecall.database.db_path", self.db_path):
            response = self.client.get("/api/timeline?page=1&limit=10")
            self.assertEqual(response.status_code, 200)
            json_data = response.get_json()

            self.assertEqual(json_data["page"], 1)
            self.assertEqual(json_data["limit"], 10)
            self.assertEqual(json_data["count"], 1)
            self.assertEqual(json_data["entries"][0]["app"], "Firefox")
            self.assertIn("human_time", json_data["entries"][0])

    def test_api_search_json(self):
        with patch("openrecall.database.db_path", self.db_path):
            response = self.client.get("/api/search?q=Tesseract")
            self.assertEqual(response.status_code, 200)
            json_data = response.get_json()

            self.assertEqual(json_data["query"], "Tesseract")
            self.assertEqual(json_data["count"], 1)
            self.assertEqual(json_data["entries"][0]["app"], "Firefox")

    def test_serve_image(self):
        with tempfile.TemporaryDirectory() as img_dir:
            test_file = os.path.join(img_dir, "test_shot.webp")
            with open(test_file, "wb") as f:
                f.write(b"RIFFdummywebpdata")

            with patch("openrecall.app.screenshots_path", img_dir):
                response = self.client.get("/static/test_shot.webp")
                self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
