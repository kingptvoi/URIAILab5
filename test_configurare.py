import contextlib
import importlib.util
import io
import json
import logging
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


SOURCE_PATH = Path(__file__).with_name("configurare.py")
SPEC = importlib.util.spec_from_file_location(
    "configurare_under_test", SOURCE_PATH
)
configurare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configurare)


class ConfigurareTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.journal_path = Path(self.temp_dir.name) / "jurnal.txt"
        self.logger = configurare.configure_logging(self.journal_path)
        self.output = io.StringIO()

    def tearDown(self):
        for handler in list(self.logger.handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                self.logger.removeHandler(handler)
        self.temp_dir.cleanup()

    def test_request_uses_cloudflare_credentials_and_parses_response(self):
        captured = {}
        body = json.dumps(
            {"success": True, "result": {"response": "răspuns simulat"}}
        ).encode("utf-8")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return body

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = request.headers
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.object(configurare, "urlopen", fake_urlopen):
            result = configurare._request_cloudflare(
                [{"role": "user", "content": "salut"}],
                "test-account",
                "test-token",
            )

        self.assertEqual(result, "răspuns simulat")
        self.assertEqual(
            captured["url"],
            configurare.API_URL_TEMPLATE.format(
                account_id="test-account", model=configurare.MODEL
            ),
        )
        self.assertTrue(
            captured["headers"]["Authorization"].startswith("Bearer ")
        )
        self.assertEqual(
            captured["payload"]["messages"],
            [{"role": "user", "content": "salut"}],
        )
        self.assertEqual(captured["payload"]["max_tokens"], 1024)

    def test_run_uses_account_id_and_token_and_logs_without_secrets(self):
        account_id = "test-account-id"
        token = "test-secret-token"
        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_ACCOUNT_ID": account_id,
                "CLOUDFLARE_API_TOKEN": token,
            },
            clear=True,
        ):
            with patch.object(
                configurare,
                "_request_cloudflare",
                return_value="răspuns Cloudflare",
            ) as request:
                with contextlib.redirect_stdout(self.output):
                    result = configurare.run_interaction(
                        "salut", logger=self.logger
                    )

        request.assert_called_once_with(
            [{"role": "user", "content": "salut"}], account_id, token
        )
        self.assertEqual(result, "răspuns Cloudflare")
        self.assertEqual(self.output.getvalue(), "")
        journal = self.journal_path.read_text(encoding="utf-8")
        self.assertIn("Prompt: salut", journal)
        self.assertIn("Răspuns: răspuns Cloudflare", journal)
        self.assertNotIn(token, journal)

    def test_missing_cloudflare_configuration_is_logged(self):
        for environment, missing_name in (
            ({"CLOUDFLARE_API_TOKEN": "token"}, "CLOUDFLARE_ACCOUNT_ID"),
            ({"CLOUDFLARE_ACCOUNT_ID": "account"}, "CLOUDFLARE_API_TOKEN"),
        ):
            with self.subTest(missing=missing_name):
                with patch.dict(os.environ, environment, clear=True):
                    with patch.object(
                        configurare, "_request_cloudflare"
                    ) as request:
                        with self.assertRaisesRegex(ValueError, missing_name):
                            configurare.run_interaction(
                                "test", logger=self.logger
                            )
                request.assert_not_called()

        journal = self.journal_path.read_text(encoding="utf-8")
        self.assertIn("ValueError", journal)

    def test_http_error_details_are_logged_without_leaking_token(self):
        token = "test-secret-token"
        error = configurare.CloudflareAPIError(
            10000, "HTTP_403", f"Invalid token {token}"
        )
        environment = {
            "CLOUDFLARE_ACCOUNT_ID": "test-account",
            "CLOUDFLARE_API_TOKEN": token,
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(
                configurare, "_request_cloudflare", side_effect=error
            ):
                with self.assertRaises(configurare.CloudflareAPIError):
                    configurare.run_interaction("test", logger=self.logger)

        journal = self.journal_path.read_text(encoding="utf-8")
        self.assertIn("code=10000", journal)
        self.assertIn("status=HTTP_403", journal)
        self.assertIn("[REDACTED]", journal)
        self.assertNotIn(token, journal)

    def test_http_error_response_is_parsed(self):
        response_body = json.dumps(
            {
                "success": False,
                "errors": [{"code": 10000, "message": "Token invalid"}],
            }
        ).encode("utf-8")
        http_error = HTTPError(
            "https://api.cloudflare.com/test",
            403,
            "Forbidden",
            {},
            BytesIO(response_body),
        )
        with patch.object(configurare, "urlopen", side_effect=http_error):
            with self.assertRaises(configurare.CloudflareAPIError) as raised:
                configurare._request_cloudflare(
                    [{"role": "user", "content": "salut"}],
                    "test-account",
                    "test-token",
                )

        self.assertEqual(raised.exception.code, 10000)
        self.assertEqual(raised.exception.status, "HTTP_403")
        self.assertEqual(raised.exception.message, "Token invalid")

    def test_api_error_in_success_http_response_is_rejected(self):
        body = json.dumps(
            {
                "success": False,
                "errors": [{"code": 1234, "message": "Quota exceeded"}],
            }
        ).encode("utf-8")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return body

        with patch.object(
            configurare, "urlopen", return_value=FakeResponse()
        ):
            with self.assertRaises(configurare.CloudflareAPIError) as raised:
                configurare._request_cloudflare(
                    [{"role": "user", "content": "salut"}],
                    "test-account",
                    "test-token",
                )

        self.assertEqual(raised.exception.code, 1234)
        self.assertEqual(raised.exception.message, "Quota exceeded")

    def test_malformed_or_empty_responses_are_rejected(self):
        for response_data in (
            {},
            {"success": True, "result": {}},
            {"success": True, "result": {"response": ""}},
        ):
            with self.subTest(response_data=response_data):
                body = json.dumps(response_data).encode("utf-8")

                class FakeResponse:
                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def read(self):
                        return body

                with patch.object(
                    configurare, "urlopen", return_value=FakeResponse()
                ):
                    with self.assertRaises(ValueError):
                        configurare._request_cloudflare(
                            [{"role": "user", "content": "salut"}],
                            "test-account",
                            "test-token",
                        )

    def test_empty_prompt_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "gol"):
            configurare.run_interaction("  ", logger=self.logger)
        self.assertIn(
            "Promptul este gol",
            self.journal_path.read_text(encoding="utf-8"),
        )

    def test_run_interaction_sends_conversation_history(self):
        history = [
            {"role": "user", "content": "Salut"},
            {"role": "assistant", "content": "Bună!"},
        ]
        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_ACCOUNT_ID": "account",
                "CLOUDFLARE_API_TOKEN": "token",
            },
            clear=True,
        ):
            with patch.object(
                configurare, "_request_cloudflare", return_value="Sunt bine."
            ) as request:
                with contextlib.redirect_stdout(self.output):
                    configurare.run_interaction(
                        "Ce mai faci?",
                        history=history,
                        logger=self.logger,
                    )

        request.assert_called_once_with(
            history + [{"role": "user", "content": "Ce mai faci?"}],
            "account",
            "token",
        )


class PrincipalTests(unittest.TestCase):
    def test_terminal_chat_keeps_history_and_exits_on_command(self):
        import principal

        history_at_each_call = []

        def fake_interaction(prompt, *, history, logger):
            history_at_each_call.append((prompt, list(history)))
            return f"răspuns la {prompt}"

        output = io.StringIO()
        with patch.object(principal, "configure_logging", return_value=None):
            with patch.object(
                principal, "run_interaction", side_effect=fake_interaction
            ):
                with patch("builtins.input", side_effect=["salut", "continuă", "exit"]):
                    with contextlib.redirect_stdout(output):
                        result = principal.main()

        self.assertEqual(result, 0)
        self.assertEqual(
            history_at_each_call,
            [
                ("salut", []),
                (
                    "continuă",
                    [
                        {"role": "user", "content": "salut"},
                        {"role": "assistant", "content": "răspuns la salut"},
                    ],
                ),
            ],
        )
        self.assertIn("│ răspuns la salut", output.getvalue())
        self.assertIn("Conversație încheiată.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
