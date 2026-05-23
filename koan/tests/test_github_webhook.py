"""Tests for GitHub webhook receiver (push-based notification triggering)."""

import hashlib
import hmac
import json
import os
import threading
import time
from http.server import HTTPServer
from unittest.mock import patch

import pytest
import urllib.request

from app.github_webhook import (
    DEFAULT_WEBHOOK_HOST,
    DEFAULT_WEBHOOK_PORT,
    MAX_BODY_BYTES,
    create_server,
    extract_repo_full_name,
    handle_event,
    is_actionable_event,
    should_trigger,
    start_webhook_server,
    verify_signature,
    write_check_signal,
)
from app.github_config import (
    get_github_webhook_enabled,
    get_github_webhook_host,
    get_github_webhook_port,
)

SECRET = "test-webhook-secret-abc123"


def _sign(payload: bytes, secret: str = SECRET) -> str:
    """Compute HMAC-SHA256 signature in GitHub's format."""
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------

class TestVerifySignature:
    def test_valid_signature(self):
        body = b'{"action":"created"}'
        sig = _sign(body)
        assert verify_signature(body, sig, SECRET) is True

    def test_wrong_secret(self):
        body = b'{"action":"created"}'
        sig = _sign(body, "wrong-secret")
        assert verify_signature(body, sig, SECRET) is False

    def test_tampered_body(self):
        body = b'{"action":"created"}'
        sig = _sign(body)
        assert verify_signature(b'{"action":"deleted"}', sig, SECRET) is False

    def test_missing_signature(self):
        assert verify_signature(b"body", "", SECRET) is False

    def test_missing_secret(self):
        assert verify_signature(b"body", "sha256=abc", "") is False

    def test_malformed_signature(self):
        assert verify_signature(b"body", "md5=abc", SECRET) is False

    def test_no_prefix(self):
        body = b"test"
        digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert verify_signature(body, digest, SECRET) is False


# ---------------------------------------------------------------------------
# extract_repo_full_name
# ---------------------------------------------------------------------------

class TestExtractRepoFullName:
    def test_normal_payload(self):
        payload = {"repository": {"full_name": "Owner/Repo"}}
        assert extract_repo_full_name(payload) == "owner/repo"

    def test_missing_repository(self):
        assert extract_repo_full_name({}) == ""

    def test_non_dict_repository(self):
        assert extract_repo_full_name({"repository": "not-a-dict"}) == ""

    def test_missing_full_name(self):
        assert extract_repo_full_name({"repository": {}}) == ""


# ---------------------------------------------------------------------------
# is_actionable_event
# ---------------------------------------------------------------------------

class TestIsActionableEvent:
    def test_issue_comment_created(self):
        assert is_actionable_event("issue_comment", {"action": "created"}) is True

    def test_issue_comment_edited(self):
        assert is_actionable_event("issue_comment", {"action": "edited"}) is False

    def test_pr_review_submitted(self):
        assert is_actionable_event("pull_request_review", {"action": "submitted"}) is True

    def test_pr_review_comment_created(self):
        assert is_actionable_event("pull_request_review_comment", {"action": "created"}) is True

    def test_commit_comment_no_action(self):
        assert is_actionable_event("commit_comment", {}) is True

    def test_issues_assigned(self):
        assert is_actionable_event("issues", {"action": "assigned"}) is True

    def test_issues_labeled(self):
        assert is_actionable_event("issues", {"action": "labeled"}) is False

    def test_pr_review_requested(self):
        assert is_actionable_event("pull_request", {"action": "review_requested"}) is True

    def test_pr_opened(self):
        assert is_actionable_event("pull_request", {"action": "opened"}) is False

    def test_push_event(self):
        assert is_actionable_event("push", {}) is False

    def test_unknown_event(self):
        assert is_actionable_event("label", {"action": "created"}) is False


# ---------------------------------------------------------------------------
# should_trigger
# ---------------------------------------------------------------------------

class TestShouldTrigger:
    def test_actionable_event_known_repo(self):
        payload = {"action": "created", "repository": {"full_name": "org/repo"}}
        assert should_trigger("issue_comment", payload, {"org/repo"}) is True

    def test_actionable_event_unknown_repo(self):
        payload = {"action": "created", "repository": {"full_name": "other/repo"}}
        assert should_trigger("issue_comment", payload, {"org/repo"}) is False

    def test_actionable_event_no_repo_filter(self):
        payload = {"action": "created", "repository": {"full_name": "any/repo"}}
        assert should_trigger("issue_comment", payload, None) is True

    def test_non_actionable_event(self):
        payload = {"action": "opened", "repository": {"full_name": "org/repo"}}
        assert should_trigger("pull_request", payload, {"org/repo"}) is False


# ---------------------------------------------------------------------------
# write_check_signal
# ---------------------------------------------------------------------------

class TestWriteCheckSignal:
    def test_writes_signal_file(self, tmp_path):
        assert write_check_signal(str(tmp_path)) is True
        signal = tmp_path / ".koan-check-notifications"
        assert signal.exists()
        assert "github webhook" in signal.read_text()

    def test_returns_false_on_bad_path(self):
        assert write_check_signal("/nonexistent/path/xyz") is False


# ---------------------------------------------------------------------------
# handle_event
# ---------------------------------------------------------------------------

class TestHandleEvent:
    def test_triggers_poll_for_actionable_event(self, tmp_path):
        payload = {"action": "created", "repository": {"full_name": "org/repo"}}
        result = handle_event("issue_comment", payload, str(tmp_path), {"org/repo"})
        assert result is True
        assert (tmp_path / ".koan-check-notifications").exists()

    def test_no_trigger_for_non_actionable(self, tmp_path):
        payload = {"action": "opened", "repository": {"full_name": "org/repo"}}
        result = handle_event("pull_request", payload, str(tmp_path), {"org/repo"})
        assert result is False
        assert not (tmp_path / ".koan-check-notifications").exists()


# ---------------------------------------------------------------------------
# create_server
# ---------------------------------------------------------------------------

class TestCreateServer:
    def test_creates_server(self, tmp_path):
        server = create_server(str(tmp_path), SECRET, port=0)
        assert isinstance(server, HTTPServer)
        server.server_close()

    def test_raises_without_secret(self, tmp_path):
        with pytest.raises(ValueError, match="secret"):
            create_server(str(tmp_path), "")

    def test_raises_with_none_secret(self, tmp_path):
        with pytest.raises(ValueError, match="secret"):
            create_server(str(tmp_path), None)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

class TestWebhookConfig:
    def test_enabled_default_false(self):
        assert get_github_webhook_enabled({}) is False

    def test_enabled_true(self):
        config = {"github": {"webhook": {"enabled": True}}}
        assert get_github_webhook_enabled(config) is True

    def test_port_default(self):
        assert get_github_webhook_port({}) == DEFAULT_WEBHOOK_PORT

    def test_port_custom(self):
        config = {"github": {"webhook": {"port": 9999}}}
        assert get_github_webhook_port(config) == 9999

    def test_port_invalid_returns_default(self):
        config = {"github": {"webhook": {"port": "abc"}}}
        assert get_github_webhook_port(config) == DEFAULT_WEBHOOK_PORT

    def test_port_out_of_range(self):
        config = {"github": {"webhook": {"port": 99999}}}
        assert get_github_webhook_port(config) == DEFAULT_WEBHOOK_PORT

    def test_host_default(self):
        assert get_github_webhook_host({}) == DEFAULT_WEBHOOK_HOST

    def test_host_custom(self):
        config = {"github": {"webhook": {"host": "0.0.0.0"}}}
        assert get_github_webhook_host(config) == "0.0.0.0"

    def test_host_empty_string(self):
        config = {"github": {"webhook": {"host": "  "}}}
        assert get_github_webhook_host(config) == DEFAULT_WEBHOOK_HOST


# ---------------------------------------------------------------------------
# End-to-end HTTP tests
# ---------------------------------------------------------------------------

class TestHTTPEndToEnd:
    """Test the actual HTTP server with real requests."""

    @pytest.fixture()
    def webhook_server(self, tmp_path):
        """Start a webhook server on a random port, yield (server, port, tmp_path)."""
        server = create_server(str(tmp_path), SECRET, port=0, host="127.0.0.1",
                               known_repos={"org/repo"})
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server, port, tmp_path
        server.shutdown()
        server.server_close()

    def _post(self, port, body, headers=None):
        """Send a POST request to the webhook server."""
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        url = f"http://127.0.0.1:{port}/"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def test_invalid_signature_returns_401(self, webhook_server):
        _, port, _ = webhook_server
        body = b'{"action":"created"}'
        status, text = self._post(port, body, {
            "X-Hub-Signature-256": "sha256=invalid",
            "X-GitHub-Event": "issue_comment",
        })
        assert status == 401
        assert "invalid signature" in text

    def test_missing_signature_returns_401(self, webhook_server):
        _, port, _ = webhook_server
        body = b'{"action":"created"}'
        status, text = self._post(port, body, {
            "X-GitHub-Event": "issue_comment",
        })
        assert status == 401

    def test_ping_returns_pong(self, webhook_server):
        _, port, _ = webhook_server
        body = b'{"zen":"something"}'
        sig = _sign(body)
        status, text = self._post(port, body, {
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "ping",
        })
        assert status == 200
        assert text == "pong"

    def test_valid_event_triggers_signal(self, webhook_server):
        _, port, tmp_path = webhook_server
        payload = {"action": "created", "repository": {"full_name": "org/repo"}}
        body = json.dumps(payload).encode()
        sig = _sign(body)
        status, text = self._post(port, body, {
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "issue_comment",
        })
        assert status == 202
        assert text == "accepted"
        assert (tmp_path / ".koan-check-notifications").exists()

    def test_unknown_repo_no_signal(self, webhook_server):
        _, port, tmp_path = webhook_server
        payload = {"action": "created", "repository": {"full_name": "other/repo"}}
        body = json.dumps(payload).encode()
        sig = _sign(body)
        status, _ = self._post(port, body, {
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "issue_comment",
        })
        assert status == 202  # still 202 (don't leak filtering)
        assert not (tmp_path / ".koan-check-notifications").exists()

    def test_get_returns_ok(self, webhook_server):
        _, port, _ = webhook_server
        url = f"http://127.0.0.1:{port}/"
        resp = urllib.request.urlopen(url)
        assert resp.status == 200
        assert resp.read().decode() == "ok"

    def test_empty_body_returns_400(self, webhook_server):
        _, port, _ = webhook_server
        url = f"http://127.0.0.1:{port}/"
        req = urllib.request.Request(url, data=b"", method="POST")
        req.add_header("Content-Length", "0")
        try:
            resp = urllib.request.urlopen(req)
            status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 400

    def test_invalid_json_returns_400(self, webhook_server):
        _, port, _ = webhook_server
        body = b"not json at all"
        sig = _sign(body)
        status, _ = self._post(port, body, {
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "issue_comment",
        })
        assert status == 400


# ---------------------------------------------------------------------------
# maybe_start_from_config
# ---------------------------------------------------------------------------

class TestMaybeStartFromConfig:
    def test_disabled_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        config_file = tmp_path / "config.yaml"
        config_file.write_text("github:\n  webhook:\n    enabled: false\n")

        from app.github_webhook import maybe_start_from_config
        with patch("app.github_webhook._resolve_known_repos", return_value=None):
            with patch("app.utils.load_config", return_value={"github": {"webhook": {"enabled": False}}}):
                result = maybe_start_from_config(str(tmp_path))
        assert result is None

    def test_enabled_no_secret_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KOAN_GITHUB_WEBHOOK_SECRET", raising=False)
        from app.github_webhook import maybe_start_from_config
        config = {"github": {"webhook": {"enabled": True}}}
        with patch("app.utils.load_config", return_value=config):
            with patch("app.github_webhook._resolve_known_repos", return_value=None):
                result = maybe_start_from_config(str(tmp_path))
        assert result is None
