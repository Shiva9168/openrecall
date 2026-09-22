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

    def test_timeline_app_and_date_filtering(self):
        with patch("openrecall.database.db_path", self.db_path):
            # Seed entry for VSCode
            insert_entry(
                text="VSCode editor line 50",
                timestamp=self.now + 10,
                app="VSCode",
                title="openrecall/app.py",
                image_path=f"{self.now + 10}_0.webp",
                target_path=self.db_path,
            )

            # Query timeline with app=Firefox filter
            response = self.client.get("/?app=Firefox")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn("Firefox", html)

            # Check app dropdown option list contains VSCode and Firefox
            self.assertIn('<option value="Firefox"', html)
            self.assertIn('<option value="VSCode"', html)

    def test_multi_monitor_badge_rendering(self):
        with patch("openrecall.database.db_path", self.db_path):
            insert_entry(
                text="Multi-monitor 2 display text",
                timestamp=self.now + 20,
                app="Terminal",
                title="Monitor 2 Display",
                image_path=f"{self.now + 20}_1.webp",
                monitor=2,
                target_path=self.db_path,
            )

            response = self.client.get("/?app=Terminal")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn("Mon 2", html)

    def test_capture_detail_route_valid_id(self):
        with patch("openrecall.database.db_path", self.db_path):
            response = self.client.get("/capture/1")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn("Capture Metadata", html)
            self.assertIn("Firefox", html)
            self.assertIn("OpenRecall GitHub Repository", html)
            self.assertIn("copyOcrText()", html)

    def test_capture_detail_route_invalid_id(self):
        with patch("openrecall.database.db_path", self.db_path):
            response = self.client.get("/capture/99999")
            self.assertEqual(response.status_code, 404)
            html = response.get_data(as_text=True)
            self.assertIn("Capture Not Found", html)

    def test_api_capture_detail_json(self):
        with patch("openrecall.database.db_path", self.db_path):
            response = self.client.get("/api/capture/1")
            self.assertEqual(response.status_code, 200)
            json_data = response.get_json()
            self.assertEqual(json_data["id"], 1)
            self.assertEqual(json_data["app"], "Firefox")

    def test_api_capture_detail_invalid_id(self):
        with patch("openrecall.database.db_path", self.db_path):
            response = self.client.get("/api/capture/99999")
            self.assertEqual(response.status_code, 404)
            json_data = response.get_json()
            self.assertIn("error", json_data)


    def test_offline_ui_no_external_cdn_links(self):
        with patch("openrecall.database.db_path", self.db_path):
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertNotIn("http://", html)
            self.assertNotIn("https://", html)
            self.assertNotIn("bootstrap.min.css", html)
            self.assertNotIn("bootstrap-icons.css", html)

    def test_api_pause_and_resume_routes(self):
        """Phase 7: Verifies POST /api/pause and POST /api/resume state toggling and JSON/form response."""
        from openrecall.privacy import get_privacy_policy
        policy = get_privacy_policy()

        # 1. Test POST /api/pause JSON
        res1 = self.client.post("/api/pause", json={})
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.get_json(), {"status": "paused", "is_paused": True})
        self.assertTrue(policy.is_paused())

        # Repeated pause call (idempotency)
        res1_repeat = self.client.post("/api/pause", json={})
        self.assertEqual(res1_repeat.status_code, 200)
        self.assertTrue(policy.is_paused())

        # 2. Test POST /api/resume JSON
        res2 = self.client.post("/api/resume", json={})
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.get_json(), {"status": "active", "is_paused": False})
        self.assertFalse(policy.is_paused())

        # Repeated resume call (idempotency)
        res2_repeat = self.client.post("/api/resume", json={})
        self.assertEqual(res2_repeat.status_code, 200)
        self.assertFalse(policy.is_paused())

        # 3. Test Form Submit redirect
        res_form = self.client.post("/api/pause")
        self.assertEqual(res_form.status_code, 302)
        self.assertTrue(policy.is_paused())

        # Reset state back to unpaused
        policy.resume()

    def test_navbar_status_badge_and_toggle_button(self):
        """Phase 7: Verifies navbar renders Recording Active vs Recording Paused badge."""
        from openrecall.privacy import get_privacy_policy
        policy = get_privacy_policy()

        with patch("openrecall.database.db_path", self.db_path):
            policy.resume()
            res_active = self.client.get("/")
            self.assertIn("Recording Active", res_active.get_data(as_text=True))
            self.assertIn("/api/pause", res_active.get_data(as_text=True))

            policy.pause()
            res_paused = self.client.get("/")
            self.assertIn("Recording Paused", res_paused.get_data(as_text=True))
            self.assertIn("/api/resume", res_paused.get_data(as_text=True))

            policy.resume()

    def test_first_run_empty_state_summary_card(self):
        """Phase 7: Verifies first-run summary card on a fresh database with 0 total records."""
        empty_dir = tempfile.TemporaryDirectory()
        empty_db = os.path.join(empty_dir.name, "empty.db")
        create_db(empty_db)

        try:
            with patch("openrecall.database.db_path", empty_db):
                res = self.client.get("/")
                self.assertEqual(res.status_code, 200)
                html = res.get_data(as_text=True)

                self.assertIn("Welcome to OpenRecall", html)
                self.assertIn("Capture Pipeline:", html)
                self.assertIn("Tesseract OCR Engine:", html)
                self.assertIn("Local Data Directory:", html)
        finally:
            empty_dir.cleanup()

    def test_filtered_empty_state_does_not_show_first_run_summary(self):
        """Phase 7: Verifies that when records exist, active filters returning 0 results show filter alert, not first-run."""
        with patch("openrecall.database.db_path", self.db_path):
            # Query non-existent app filter
            res = self.client.get("/?app=NonExistentApp999")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)

            self.assertNotIn("Welcome to OpenRecall", html)
            self.assertIn("No timeline records found", html)
            self.assertIn("No desktop screen captures match your active filters.", html)


if __name__ == "__main__":
    unittest.main()


