"""Security and privacy unit and integration test suite for OpenRecall.

Verifies XSS auto-escaping in templates, path traversal defense in static image serving,
SQL and FTS5 injection resistance, and zero leakage of sensitive OCR/title data in health endpoints.
"""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from openrecall.app import app
from openrecall.database import create_db, insert_entry, get_entry_by_id, search_entries


class TestSecurityAndPrivacy(unittest.TestCase):
    """Test suite verifying application security boundaries and privacy guarantees."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_recall.db")
        create_db(self.db_path)

        app.config["TESTING"] = True
        self.client = app.test_client()

        self.now = int(time.time())
        # Seed an entry with malicious XSS content in text, app, and title
        with patch("openrecall.database.db_path", self.db_path):
            self.entry_id = insert_entry(
                text="<script>alert('XSS_TEXT_INJECTION')</script> Sensitive password snippet",
                timestamp=self.now,
                app="<iframe src='http://evil.com'>App</iframe>",
                title="<img src=x onerror=alert('XSS_TITLE_INJECTION')>",
                image_path=f"{self.now}_0.webp",
                target_path=self.db_path,
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_xss_html_autoescaping_in_rendered_templates(self):
        """Verifies that user OCR text, window titles, and app names are auto-escaped in HTML views."""
        with patch("openrecall.database.db_path", self.db_path):
            # 1. Timeline route HTML auto-escaping
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)

            self.assertNotIn("<script>alert('XSS_TEXT_INJECTION')</script>", html)
            self.assertIn("&lt;script&gt;alert(&#39;XSS_TEXT_INJECTION&#39;)&lt;/script&gt;", html)

            # 2. Gallery mode HTML auto-escaping
            response_gallery = self.client.get("/?mode=gallery")
            self.assertEqual(response_gallery.status_code, 200)
            gallery_html = response_gallery.get_data(as_text=True)
            self.assertNotIn("<iframe src='http://evil.com'>", gallery_html)

            # 3. Search route HTML auto-escaping
            response_search = self.client.get("/search?q=XSS_TEXT_INJECTION")
            self.assertEqual(response_search.status_code, 200)
            search_html = response_search.get_data(as_text=True)
            self.assertNotIn("<script>alert('XSS_TEXT_INJECTION')</script>", search_html)

            # 3. Capture detail page HTML auto-escaping
            response_detail = self.client.get(f"/capture/{self.entry_id}")
            self.assertEqual(response_detail.status_code, 200)
            detail_html = response_detail.get_data(as_text=True)
            self.assertNotIn("<script>alert('XSS_TEXT_INJECTION')</script>", detail_html)
            self.assertIn("&lt;script&gt;alert(&#39;XSS_TEXT_INJECTION&#39;)&lt;/script&gt;", detail_html)

    def test_path_traversal_defense_static_route(self):
        """Verifies that directory traversal payloads in static route are blocked safely with 400 or 404."""
        traversal_payloads = [
            "/static/../recall.db",
            "/static/../../etc/passwd",
            "/static/..%2f..%2fetc/passwd",
            "/static/%2e%2e%2frecall.db",
            "/static/....//....//etc/passwd",
        ]
        for payload in traversal_payloads:
            response = self.client.get(payload)
            self.assertIn(
                response.status_code,
                [400, 404],
                f"Payload {payload} was not blocked properly (status: {response.status_code})",
            )
            # Ensure no system file content is returned
            self.assertNotIn("root:x:0:0", response.get_data(as_text=True))

    def test_sql_fts_injection_resistance(self):
        """Verifies that SQL and FTS5 injection payloads in search queries do not crash or corrupt database."""
        injection_payloads = [
            "' OR 1=1 --",
            '" OR ""="',
            "'; DROP TABLE entries; --",
            "' UNION SELECT 1,2,3,4,5,6,7,8 --",
            "MATCH '\"' AND 1=1",
            "NEAR(text, 10)",
            "text: (<script>)",
        ]

        with patch("openrecall.database.db_path", self.db_path):
            for payload in injection_payloads:
                # 1. Test HTML search route
                response = self.client.get(f"/search?q={payload}")
                self.assertEqual(
                    response.status_code,
                    200,
                    f"FTS/SQL injection query '{payload}' resulted in status {response.status_code}",
                )

                # 2. Test JSON search REST API route
                response_json = self.client.get(f"/api/search?q={payload}")
                self.assertEqual(
                    response_json.status_code,
                    200,
                    f"API search query '{payload}' resulted in status {response_json.status_code}",
                )

            # Verify table structure remains completely intact and queryable
            entry = get_entry_by_id(self.entry_id)
            self.assertIsNotNone(entry, "Database table was corrupted or dropped by injection payload")
            self.assertEqual(entry.id, self.entry_id)

    def test_health_api_privacy_non_leakage(self):
        """Verifies that /api/health returns operational metrics only and zero OCR text or window title data."""
        with patch("openrecall.database.db_path", self.db_path):
            response = self.client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()

            self.assertIsInstance(data, dict)
            # Standard health metric keys
            self.assertIn("status", data)
            self.assertIn("capture_thread_alive", data)
            self.assertIn("worker_thread_alive", data)
            self.assertIn("queue_depth", data)

            raw_text = str(data)
            self.assertNotIn("XSS_TEXT_INJECTION", raw_text)
            self.assertNotIn("XSS_TITLE_INJECTION", raw_text)
            self.assertNotIn("Sensitive password snippet", raw_text)
