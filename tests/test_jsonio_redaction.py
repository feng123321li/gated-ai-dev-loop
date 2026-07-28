from __future__ import annotations

import unittest

from hdg.jsonio import redact, rendered_json


class JsonIoRedactionTests(unittest.TestCase):
    def test_key_like_business_fields_are_not_redacted_by_substring(self) -> None:
        payload = {
            "exactTopLevelKeys": ["delivery", "capability"],
            "monkeyPatch": "enabled",
            "hockeyScore": 3,
            "secretaryName": "Ada",
        }

        self.assertEqual(redact(payload), payload)

    def test_boundary_sensitive_key_names_remain_redacted(self) -> None:
        payload = {
            "apiKey": "api-secret",
            "privateKey": "private-secret",
            "proxyAuthorization": "Bearer auth-secret",
            "set-cookie": "sid=cookie-secret",
            "databaseCredentials": {"username": "operator"},
        }

        safe = redact(payload)

        for key in payload:
            with self.subTest(key=key):
                self.assertEqual(safe[key], "[REDACTED]")

    def test_sensitive_header_and_credential_keys_are_redacted_recursively(self) -> None:
        payload = {
            "Authorization": "Bearer authorization-secret",
            "set-cookie": "session=cookie-secret",
            "databaseCredentials": {
                "username": "operator",
                "password": "password-secret",
            },
            "nested": [
                {
                    "proxyAuthorization": "Basic proxy-secret",
                    "ordinary": "visible",
                }
            ],
        }

        safe = redact(payload)

        self.assertEqual(safe["Authorization"], "[REDACTED]")
        self.assertEqual(safe["set-cookie"], "[REDACTED]")
        self.assertEqual(safe["databaseCredentials"], "[REDACTED]")
        self.assertEqual(safe["nested"][0]["proxyAuthorization"], "[REDACTED]")
        self.assertEqual(safe["nested"][0]["ordinary"], "visible")

    def test_common_secrets_embedded_in_free_text_are_redacted(self) -> None:
        message = (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature; "
            "access_token=access-secret, api-key: key-secret, "
            'credential="credential-secret", Cookie: sid=cookie-secret'
        )

        safe = redact(message)

        self.assertIn("Authorization: [REDACTED]", safe)
        self.assertIn("access_token=[REDACTED]", safe)
        self.assertIn("api-key: [REDACTED]", safe)
        self.assertIn('credential="[REDACTED]"', safe)
        self.assertIn("Cookie: [REDACTED]", safe)
        for secret in (
            "eyJhbGciOiJIUzI1NiJ9.payload.signature",
            "access-secret",
            "key-secret",
            "credential-secret",
            "cookie-secret",
        ):
            self.assertNotIn(secret, safe)

    def test_environment_credentials_and_known_tokens_are_redacted(
        self,
    ) -> None:
        slack_token = "xoxb-" + "123456789012-" + "abcdefghijklmnopqrstuvwx"
        message = (
            "AWS_SECRET_ACCESS_KEY=synthetic-aws-secret-123456 "
            "GITLAB_TOKEN=synthetic-gitlab-secret-123456 "
            "OPENAI_API_KEY=synthetic-openai-secret-123456 "
            "SLACK_BOT_TOKEN=synthetic-slack-secret-123456 "
            "standalone glpat-abcdefghijklmnopqrst and "
            f"{slack_token}"
        )

        safe = redact(message)

        for secret in (
            "synthetic-aws-secret-123456",
            "synthetic-gitlab-secret-123456",
            "synthetic-openai-secret-123456",
            "synthetic-slack-secret-123456",
            "glpat-abcdefghijklmnopqrst",
            slack_token,
        ):
            with self.subTest(secret_kind=secret.split("-")[0]):
                self.assertNotIn(secret, safe)
        self.assertEqual(safe.count("[REDACTED]"), 6)

    def test_standalone_bearer_token_is_redacted_without_hiding_safe_text(self) -> None:
        message = "upstream returned Bearer opaque-token-value while retry remained safe"

        safe = redact(message)

        self.assertEqual(
            safe,
            "upstream returned Bearer [REDACTED] while retry remained safe",
        )

    def test_local_absolute_paths_in_free_text_are_redacted(self) -> None:
        payload = {
            "windows": r"failed at C:\Users\alice\private\config.toml:12",
            "windows_slashes": "loaded G:/projects/private/.env",
            "windows_spaces": r"G:\Project With Space\secret.txt",
            "unc": r"copied \\server\share\private\state.db",
            "linux": "failed at /home/alice/private/config.toml",
            "container": "failed at /app/private/config.toml",
            "generic_container": "/project/private/config.toml",
            "gitlab_runner": "/builds/group/project/private.env",
            "github_runner": "/github/workspace/private.env",
            "mac": "loaded /Users/alice/private/.env",
            "relative": "src/hdg/jsonio.py",
            "url": "https://example.test/api/v1",
        }

        safe = redact(payload)

        for key in (
            "windows",
            "windows_slashes",
            "windows_spaces",
            "unc",
            "linux",
            "container",
            "generic_container",
            "gitlab_runner",
            "github_runner",
            "mac",
        ):
            self.assertIn("[REDACTED_PATH]", safe[key])
        self.assertNotIn(r"C:\Users\alice", safe["windows"])
        self.assertNotIn("G:/projects/private", safe["windows_slashes"])
        self.assertNotIn("With Space", safe["windows_spaces"])
        self.assertNotIn(r"\\server\share", safe["unc"])
        self.assertNotIn("/home/alice", safe["linux"])
        self.assertNotIn("/app/private", safe["container"])
        self.assertNotIn("/project/private", safe["generic_container"])
        self.assertNotIn("/builds/group", safe["gitlab_runner"])
        self.assertNotIn("/github/workspace", safe["github_runner"])
        self.assertNotIn("/Users/alice", safe["mac"])
        self.assertEqual(safe["relative"], "src/hdg/jsonio.py")
        self.assertEqual(safe["url"], "https://example.test/api/v1")

    def test_rendered_json_keeps_shape_and_applies_free_text_redaction(self) -> None:
        rendered = rendered_json(
            {
                "status": "failed",
                "message": r"token=secret-value at C:\Users\alice\state.db",
            }
        )

        self.assertEqual(
            rendered,
            '{"status":"failed","message":"token=[REDACTED] at '
            '[REDACTED_PATH]"}\n',
        )


if __name__ == "__main__":
    unittest.main()
