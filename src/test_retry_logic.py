"""
Unit Tests for Gemini Retry Logic
Validates that call_gemini_with_retry correctly handles:
- 429 (Rate Limit) with exponential backoff
- 500/503 (Server Error) with 30s cooldown + single retry
- Successful responses after retries
- Exhausted retries
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# We need to patch argparse before importing the module
with patch('sys.argv', ['test_retry_logic.py']):
    from main import (
        call_gemini_with_retry,
        strip_html_js,
        verify_credentials,
        GEMINI_MAX_RETRIES,
        GEMINI_BACKOFF_BASE,
        GEMINI_COOLDOWN_SECONDS,
    )


class TestExponentialBackoff429(unittest.TestCase):
    """Tests for 429 (Resource Exhausted) handling."""

    @patch('main.time.sleep')
    def test_retries_on_429_then_succeeds(self, mock_sleep):
        """Simulate 2 rate-limit failures, then success on 3rd attempt."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"result": "success"}'
        
        # First 2 calls raise 429, third succeeds
        mock_client.models.generate_content.side_effect = [
            Exception("429 Resource Exhausted"),
            Exception("429 Resource Exhausted"),
            mock_response,
        ]
        
        result = call_gemini_with_retry(mock_client, "test prompt")
        
        self.assertEqual(result, '{"result": "success"}')
        self.assertEqual(mock_client.models.generate_content.call_count, 3)
        
        # Verify exponential backoff: 2^1=2s, 2^2=4s
        expected_waits = [
            call(GEMINI_BACKOFF_BASE ** 1),  # 2s
            call(GEMINI_BACKOFF_BASE ** 2),  # 4s
        ]
        mock_sleep.assert_has_calls(expected_waits)
        print("[PASS] 429 retry with exponential backoff works correctly")

    @patch('main.time.sleep')
    def test_exhausts_retries_on_persistent_429(self, mock_sleep):
        """Simulate persistent 429 errors until all retries are exhausted."""
        mock_client = MagicMock()
        
        # All calls raise 429
        mock_client.models.generate_content.side_effect = [
            Exception("429 Resource Exhausted")
        ] * GEMINI_MAX_RETRIES
        
        with self.assertRaises(Exception) as ctx:
            call_gemini_with_retry(mock_client, "test prompt")
        
        self.assertIn("429", str(ctx.exception))
        self.assertEqual(mock_client.models.generate_content.call_count, GEMINI_MAX_RETRIES)
        
        # Verify all backoff waits happened
        expected_waits = [
            call(GEMINI_BACKOFF_BASE ** i) for i in range(1, GEMINI_MAX_RETRIES + 1)
        ]
        mock_sleep.assert_has_calls(expected_waits)
        print(f"[PASS] Exhausted all {GEMINI_MAX_RETRIES} retries on persistent 429")

    @patch('main.time.sleep')
    def test_backoff_timing_is_exponential(self, mock_sleep):
        """Verify wait times double each attempt: 2, 4, 8, 16, 32."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            Exception("429 Resource Exhausted")
        ] * GEMINI_MAX_RETRIES
        
        try:
            call_gemini_with_retry(mock_client, "test prompt")
        except Exception:
            pass
        
        actual_waits = [c.args[0] for c in mock_sleep.call_args_list]
        expected_waits = [2, 4, 8, 16, 32]
        
        self.assertEqual(actual_waits, expected_waits)
        print(f"[PASS] Backoff times are exponential: {actual_waits}")


class TestServerError500503(unittest.TestCase):
    """Tests for 500/503 (Server Error) handling."""

    @patch('main.time.sleep')
    def test_500_cooldown_then_success(self, mock_sleep):
        """Simulate 500 error, 30s cooldown, then success."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"meal": "Kylling med ris"}'
        
        mock_client.models.generate_content.side_effect = [
            Exception("500 Internal Server Error"),
            mock_response,
        ]
        
        result = call_gemini_with_retry(mock_client, "test prompt")
        
        self.assertEqual(result, '{"meal": "Kylling med ris"}')
        self.assertEqual(mock_client.models.generate_content.call_count, 2)
        
        # Verify 30-second cooldown
        mock_sleep.assert_called_once_with(GEMINI_COOLDOWN_SECONDS)
        print(f"[PASS] 500 error triggers {GEMINI_COOLDOWN_SECONDS}s cooldown, then succeeds")

    @patch('main.time.sleep')
    def test_503_fails_after_single_retry(self, mock_sleep):
        """503 should only retry ONCE after cooldown, then fail."""
        mock_client = MagicMock()
        
        mock_client.models.generate_content.side_effect = [
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
        ]
        
        with self.assertRaises(Exception) as ctx:
            call_gemini_with_retry(mock_client, "test prompt")
        
        self.assertIn("503", str(ctx.exception))
        self.assertEqual(mock_client.models.generate_content.call_count, 2)
        
        # Only ONE cooldown wait (not exponential)
        mock_sleep.assert_called_once_with(GEMINI_COOLDOWN_SECONDS)
        print("[PASS] 503 only retries once after cooldown, then gives up")


class TestOtherErrors(unittest.TestCase):
    """Tests for non-retryable errors."""

    @patch('main.time.sleep')
    def test_auth_error_fails_immediately(self, mock_sleep):
        """Authentication errors should NOT be retried."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("401 Unauthorized: Invalid API Key")
        
        with self.assertRaises(Exception) as ctx:
            call_gemini_with_retry(mock_client, "test prompt")
        
        self.assertIn("401", str(ctx.exception))
        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        mock_sleep.assert_not_called()
        print("[PASS] 401 auth error fails immediately without retries")

    @patch('main.time.sleep')
    def test_success_on_first_attempt(self, mock_sleep):
        """Normal success should not trigger any retries."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"result": "ok"}'
        mock_client.models.generate_content.return_value = mock_response
        
        result = call_gemini_with_retry(mock_client, "test prompt")
        
        self.assertEqual(result, '{"result": "ok"}')
        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        mock_sleep.assert_not_called()
        print("[PASS] Success on first attempt, no retries needed")


class TestStripHtmlJs(unittest.TestCase):
    """Tests for the HTML/JS stripping (Token Squeezer)."""

    def test_strips_html_tags(self):
        html = "<div><p>Kylling <b>500g</b></p><span>29 kr</span></div>"
        result = strip_html_js(html)
        self.assertNotIn("<div>", result)
        self.assertNotIn("<p>", result)
        self.assertIn("Kylling", result)
        self.assertIn("29", result)
        print("[PASS] HTML tags stripped correctly")

    def test_strips_script_blocks(self):
        html = '<p>Deals</p><script>var x = 1; function doStuff(){}</script><p>More deals</p>'
        result = strip_html_js(html)
        self.assertNotIn("doStuff", result)
        self.assertIn("Deals", result)
        self.assertIn("More deals", result)
        print("[PASS] Script blocks stripped correctly")

    def test_strips_style_blocks(self):
        html = '<style>.red { color: red; }</style><p>Tilbud: Mælk 12 kr</p>'
        result = strip_html_js(html)
        self.assertNotIn(".red", result)
        self.assertIn("Mælk", result)
        print("[PASS] Style blocks stripped correctly")

    def test_404_invalid_model_fails_immediately(self):
        """404 errors (NotFound) should NOT be retried and should call sys.exit."""
        from google.api_core import exceptions as api_exceptions
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = api_exceptions.NotFound("404 Model Not Found")
        
        with self.assertRaises(SystemExit) as ctx:
            call_gemini_with_retry(mock_client, "test prompt")
        
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        print("[PASS] 404 invalid model fails immediately with exit(1)")

    def test_empty_input(self):
        self.assertEqual(strip_html_js(""), "")
        self.assertEqual(strip_html_js(None), "")
        print("[PASS] Empty input handled correctly")

    def test_plain_text_unchanged(self):
        plain = "Kylling 500g 49.95 kr"
        result = strip_html_js(plain)
        self.assertIn("Kylling", result)
        self.assertIn("49.95", result)
        print("[PASS] Plain text passes through unchanged")


class TestCredentialVerification(unittest.TestCase):
    """Tests for credential pre-flight check."""

    @patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False)
    def test_missing_api_key_exits(self):
        """Missing API key should cause sys.exit(1)."""
        with self.assertRaises(SystemExit) as ctx:
            verify_credentials()
        self.assertEqual(ctx.exception.code, 1)
        print("[PASS] Missing API key causes exit(1)")

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-abc123"}, clear=False)
    @patch('os.path.exists', return_value=False)
    def test_missing_credentials_file_exits(self, mock_exists):
        """Missing credentials.json should cause sys.exit(1)."""
        with self.assertRaises(SystemExit) as ctx:
            verify_credentials()
        self.assertEqual(ctx.exception.code, 1)
        print("[PASS] Missing credentials.json causes exit(1)")


if __name__ == "__main__":
    print("=" * 60)
    print("  RETRY LOGIC UNIT TESTS")
    print("=" * 60)
    unittest.main(verbosity=2)
