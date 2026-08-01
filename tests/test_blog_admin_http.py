import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.blog_admin_server as admin


class BlogAdminHTTPTests(unittest.TestCase):
    username = "admin"
    password = "correct horse battery staple"
    origin = "http://admin.test"

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        source = root / "source"
        source.mkdir()
        access_log = root / "access.log"
        access_log.write_text("", encoding="utf-8")
        cls.revocation_path = root / "session-revocations.jsonl"
        cls.todo_path = root / "todos.json"
        password_hash = admin.hash_password(cls.password, iterations=200_000)
        cls.environment = patch.dict(
            os.environ,
            {
                "BLOG_ADMIN_USER": cls.username,
                "BLOG_ADMIN_PASSWORD_HASH": password_hash,
                "BLOG_ADMIN_SESSION_SECRET": "http-session-test-secret",
                "BLOG_ADMIN_ANALYTICS_SECRET": "http-analytics-test-secret",
                "BLOG_ADMIN_ALLOWED_ORIGINS": cls.origin,
                "BLOG_ADMIN_AUDIT_LOG": str(root / "audit.log"),
                "BLOG_ADMIN_REVOCATION_FILE": str(cls.revocation_path),
                "BLOG_ADMIN_TODO_FILE": str(cls.todo_path),
                "BLOG_ADMIN_ACCESS_LOG": str(access_log),
                "BLOG_ADMIN_SOURCE_DIR": str(source),
                "BLOG_ADMIN_REPO_URL": "",
                "BLOG_ADMIN_UPDATE_SUBMODULES": "0",
                "BLOG_ADMIN_TIMEZONE": "UTC",
            },
        )
        cls.environment.start()
        cls.server = admin.BlogAdminHTTPServer(("127.0.0.1", 0), admin.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host, cls.port = cls.server.server_address

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.environment.stop()
        cls.temporary.cleanup()

    def setUp(self):
        admin.login_failures.clear()
        admin.revoked_sessions.clear()
        admin._revocations_loaded_file = None
        self.revocation_path.unlink(missing_ok=True)
        self.todo_path.unlink(missing_ok=True)

    def tearDown(self):
        admin.login_failures.clear()
        admin.revoked_sessions.clear()
        admin._revocations_loaded_file = None
        self.revocation_path.unlink(missing_ok=True)
        self.todo_path.unlink(missing_ok=True)

    def request(self, method, path, *, payload=None, raw_body=None, headers=None):
        request_headers = dict(headers or {})
        body = None
        if payload is not None and raw_body is not None:
            raise ValueError("payload and raw_body are mutually exclusive")
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
            request_headers["Content-Length"] = str(len(body))
        elif raw_body is not None:
            body = raw_body
            request_headers["Content-Length"] = str(len(body))
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
            }
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
            return response.status, response_headers, decoded
        finally:
            connection.close()

    def login(self):
        status, headers, payload = self.request(
            "POST",
            "/api/login",
            payload={"username": self.username, "password": self.password},
            headers={
                "Origin": self.origin,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["authenticated"])
        return headers["set-cookie"].split(";", 1)[0], payload["csrfToken"]

    def authenticated_headers(self):
        cookie, csrf_token = self.login()
        return {
            "Cookie": cookie,
            "Origin": self.origin,
            "Sec-Fetch-Site": "same-origin",
            "X-CSRF-Token": csrf_token,
        }

    @staticmethod
    def multipart_image(
        content: bytes,
        *,
        filename: str = "cover.png",
        mime_type: str = "image/png",
    ):
        boundary = "----BlogAdminBoundary7MA4YWxk"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n"
            "\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
        return body, f"multipart/form-data; boundary={boundary}"

    def test_session_endpoint_is_public_but_reports_logged_out(self):
        status, headers, payload = self.request("GET", "/api/session")

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"authenticated": False})
        self.assertIn("blog_admin_session=", headers["set-cookie"])
        self.assertIn("Max-Age=0", headers["set-cookie"])

    def test_protected_endpoint_returns_401_without_session(self):
        status, _, payload = self.request("GET", "/api/posts")

        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "unauthorized")

    def test_login_sets_secure_cookie_and_session_can_be_read(self):
        cookie, _ = self.login()
        status, login_headers, _ = self.request(
            "POST",
            "/api/login",
            payload={"username": self.username, "password": self.password},
            headers={"Origin": self.origin},
        )
        self.assertEqual(status, 200)
        set_cookie = login_headers["set-cookie"]
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Secure", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        self.assertIn("Path=/admin/", set_cookie)

        status, _, payload = self.request(
            "GET", "/api/session", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["user"], self.username)
        self.assertTrue(payload["csrfToken"])

    def test_write_requires_csrf_and_logout_revokes_session(self):
        cookie, csrf_token = self.login()
        common_headers = {
            "Cookie": cookie,
            "Origin": self.origin,
            "Sec-Fetch-Site": "same-origin",
        }

        status, _, payload = self.request(
            "POST", "/api/logout", payload={}, headers=common_headers
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "invalid_csrf")

        status, headers, payload = self.request(
            "POST",
            "/api/logout",
            payload={},
            headers={**common_headers, "X-CSRF-Token": csrf_token},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        self.assertIn("Max-Age=0", headers["set-cookie"])

        status, _, payload = self.request(
            "GET", "/api/session", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"authenticated": False})

    def test_logout_revocation_survives_an_in_memory_restart(self):
        cookie, csrf_token = self.login()
        status, _, payload = self.request(
            "POST",
            "/api/logout",
            payload={},
            headers={
                "Cookie": cookie,
                "Origin": self.origin,
                "Sec-Fetch-Site": "same-origin",
                "X-CSRF-Token": csrf_token,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        self.assertTrue(self.revocation_path.exists())

        with admin.login_lock:
            admin.revoked_sessions.clear()
            admin._revocations_loaded_file = None

        status, _, payload = self.request(
            "GET", "/api/session", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"authenticated": False})
        self.assertTrue(admin.revoked_sessions)

    def test_cross_origin_login_is_rejected(self):
        status, _, payload = self.request(
            "POST",
            "/api/login",
            payload={"username": self.username, "password": self.password},
            headers={"Origin": "https://evil.example"},
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "invalid_origin")

    def test_image_upload_requires_csrf_and_passes_validated_multipart(self):
        content = b"valid-image-content"
        body, content_type = self.multipart_image(content)
        cookie, csrf_token = self.login()
        common = {
            "Cookie": cookie,
            "Origin": self.origin,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": content_type,
        }

        status, _, payload = self.request(
            "POST",
            "/api/uploads/images",
            raw_body=body,
            headers=common,
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "invalid_csrf")

        expected = {
            "url": "/uploads/2026/07/random.png",
            "markdown": "![cover](/uploads/2026/07/random.png)",
            "filename": "random.png",
            "mimeType": "image/png",
            "size": len(content),
            "commit": "abc123",
        }
        with patch.object(admin, "upload_image", return_value=expected) as upload:
            status, _, payload = self.request(
                "POST",
                "/api/uploads/images",
                raw_body=body,
                headers={**common, "X-CSRF-Token": csrf_token},
            )

        self.assertEqual(status, 201)
        self.assertEqual(payload, expected)
        upload.assert_called_once_with("cover.png", "image/png", content)

    def test_image_upload_rejects_non_multipart_after_authentication(self):
        headers = self.authenticated_headers()
        status, _, payload = self.request(
            "POST",
            "/api/uploads/images",
            raw_body=b"not multipart",
            headers={**headers, "Content-Type": "application/octet-stream"},
        )

        self.assertEqual(status, 415)
        self.assertEqual(payload["code"], "invalid_content_type")

    def test_todo_crud_stats_and_not_found_http_contract(self):
        headers = self.authenticated_headers()
        status, _, created = self.request(
            "POST",
            "/api/todos",
            payload={"title": "完成后端", "date": "2026-07-28"},
            headers=headers,
        )
        self.assertEqual(status, 201)
        todo_id = created["id"]

        status, _, payload = self.request(
            "GET",
            "/api/todos?date=2026-07-28",
            headers={"Cookie": headers["Cookie"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["todos"][0]["title"], "完成后端")

        status, _, updated = self.request(
            "PUT",
            f"/api/todos/{todo_id}",
            payload={"completed": True},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(updated["completed"])
        self.assertTrue(updated["completedAt"])

        status, _, stats = self.request(
            "GET",
            "/api/todos/stats?days=1&endDate=2026-07-28",
            headers={"Cookie": headers["Cookie"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(stats["totals"]["total"], 1)
        self.assertEqual(stats["totals"]["completed"], 1)
        self.assertEqual(stats["daily"][0]["completionRate"], 100)

        status, _, deleted = self.request(
            "DELETE", f"/api/todos/{todo_id}", headers=headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(deleted, {"ok": True, "id": todo_id})
        status, _, missing = self.request(
            "DELETE", f"/api/todos/{todo_id}", headers=headers
        )
        self.assertEqual(status, 404)
        self.assertEqual(missing["code"], "todo_not_found")

    def test_todo_write_rejects_missing_csrf_and_invalid_date(self):
        cookie, csrf_token = self.login()
        common = {
            "Cookie": cookie,
            "Origin": self.origin,
            "Sec-Fetch-Site": "same-origin",
        }
        status, _, payload = self.request(
            "POST",
            "/api/todos",
            payload={"title": "unsafe", "date": "2026-07-28"},
            headers=common,
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "invalid_csrf")

        status, _, payload = self.request(
            "POST",
            "/api/todos",
            payload={"title": "bad date", "date": "2026-02-30"},
            headers={**common, "X-CSRF-Token": csrf_token},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "invalid_todo_date")

    def test_recurring_todo_plan_http_contract(self):
        headers = self.authenticated_headers()
        status, _, created = self.request(
            "POST",
            "/api/todo-plans",
            payload={
                "title": "工作日复盘",
                "repeatType": "weekly",
                "startDate": "2026-07-27",
                "endDate": "2026-07-31",
                "weekdays": [1, 2, 3, 4, 5],
            },
            headers=headers,
        )
        self.assertEqual(status, 201)
        plan_id = created["id"]
        self.assertEqual(created["recurrence"]["weekdays"], [1, 2, 3, 4, 5])

        status, _, listed = self.request(
            "GET",
            "/api/todos?date=2026-07-29",
            headers={"Cookie": headers["Cookie"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(listed["summary"]["total"], 1)
        self.assertEqual(listed["todos"][0]["planId"], plan_id)
        self.assertFalse(listed["todos"][0]["completed"])

        status, _, completed = self.request(
            "PUT",
            f"/api/todo-plans/{plan_id}/occurrences/2026-07-29",
            payload={"completed": True},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(completed["completed"])

        status, _, stats = self.request(
            "GET",
            "/api/todos/stats?days=5&endDate=2026-07-31",
            headers={"Cookie": headers["Cookie"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(stats["totals"]["total"], 5)
        self.assertEqual(stats["totals"]["completed"], 1)

        status, _, updated = self.request(
            "PUT",
            f"/api/todo-plans/{plan_id}",
            payload={"title": "晚间复盘", "weekdays": [1, 3, 5]},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["title"], "晚间复盘")

        status, _, deleted = self.request(
            "DELETE", f"/api/todo-plans/{plan_id}", headers=headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(deleted, {"ok": True, "id": plan_id})


if __name__ == "__main__":
    unittest.main()
