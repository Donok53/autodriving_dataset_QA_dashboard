from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import traceback
import urllib.error
import urllib.request
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_recent_issue_fingerprints: dict[str, float] = {}
_issue_lock = Lock()
_issue_count = 0


def report_unexpected_error(exc: Exception, context: dict[str, Any] | None = None) -> str | None:
    if not _auto_issue_enabled():
        logger.warning("auto_issue_skipped reason=disabled")
        return None

    repository = _repository_name()
    token = os.getenv("GITHUB_ISSUE_TOKEN", "").strip()
    logger.info(
        "auto_issue_attempt repository_configured=%s token_configured=%s",
        bool(repository),
        bool(token),
    )
    if not repository or not token:
        logger.warning("auto_issue_skipped reason=missing_github_issue_repository_or_token")
        return None

    context = context or {}
    fingerprint = _error_fingerprint(exc, context)
    if _should_skip_fingerprint(fingerprint):
        logger.info("auto_issue_skipped reason=duplicate fingerprint=%s", fingerprint)
        return None

    title = _issue_title(exc, context)
    body = _issue_body(exc, context, fingerprint)
    labels = _issue_labels()
    issue_url = _create_issue(repository, token, title, body, labels)
    if issue_url is None and labels:
        issue_url = _create_issue(repository, token, title, body, [])

    if issue_url:
        _mark_fingerprint_reported(fingerprint)
        logger.error("auto_issue_created url=%s fingerprint=%s", issue_url, fingerprint)
    else:
        logger.warning("auto_issue_failed reason=no_issue_url fingerprint=%s", fingerprint)
    return issue_url


def _auto_issue_enabled() -> bool:
    return os.getenv("AUTO_CREATE_GITHUB_ISSUES", "false").lower() in {"1", "true", "yes", "on"}


def _repository_name() -> str:
    return os.getenv("GITHUB_ISSUE_REPOSITORY", os.getenv("GITHUB_REPOSITORY", "")).strip()


def _issue_labels() -> list[str]:
    raw_labels = os.getenv("GITHUB_ISSUE_LABELS", "bug")
    return [label.strip() for label in raw_labels.split(",") if label.strip()]


def _cooldown_seconds() -> int:
    try:
        return max(0, int(os.getenv("AUTO_ISSUE_COOLDOWN_SECONDS", "3600")))
    except ValueError:
        return 3600


def _max_issues_per_runtime() -> int:
    try:
        return max(0, int(os.getenv("AUTO_ISSUE_MAX_PER_RUNTIME", "5")))
    except ValueError:
        return 5


def _should_skip_fingerprint(fingerprint: str) -> bool:
    now = time.time()
    cooldown = _cooldown_seconds()
    max_issues = _max_issues_per_runtime()
    with _issue_lock:
        stale_fingerprints = [
            stored_fingerprint
            for stored_fingerprint, created_at in _recent_issue_fingerprints.items()
            if now - created_at > cooldown
        ]
        for stored_fingerprint in stale_fingerprints:
            del _recent_issue_fingerprints[stored_fingerprint]

        if max_issues and _issue_count >= max_issues:
            logger.warning("auto_issue_skipped reason=max_issues_per_runtime limit=%s", max_issues)
            return True
        if fingerprint in _recent_issue_fingerprints:
            return True

        return False


def _mark_fingerprint_reported(fingerprint: str) -> None:
    global _issue_count

    with _issue_lock:
        _recent_issue_fingerprints[fingerprint] = time.time()
        _issue_count += 1


def _error_fingerprint(exc: Exception, context: dict[str, Any]) -> str:
    basis = {
        "error_type": type(exc).__name__,
        "message": _redact_text(str(exc)),
        "stage": context.get("stage"),
        "path": context.get("path"),
        "source_type": context.get("source_type"),
    }
    encoded_basis = json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded_basis).hexdigest()[:16]


def _issue_title(exc: Exception, context: dict[str, Any]) -> str:
    stage = context.get("stage") or context.get("path") or "runtime"
    title = f"[Render 오류] {type(exc).__name__} at {stage}"
    return title[:120]


def _issue_body(exc: Exception, context: dict[str, Any], fingerprint: str) -> str:
    safe_context = {
        key: _redact_text(str(value))
        for key, value in context.items()
        if value is not None and key not in {"filename", "source_name"}
    }
    traceback_text = _redact_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    if len(traceback_text) > 5000:
        traceback_text = f"{traceback_text[:5000]}\n... truncated ..."

    return "\n".join(
        [
            "Render 실행 중 예상하지 못한 서버 오류가 발생했습니다.",
            "",
            f"- fingerprint: `{fingerprint}`",
            f"- error_type: `{type(exc).__name__}`",
            f"- service: `{os.getenv('RENDER_SERVICE_NAME', 'local')}`",
            f"- commit: `{os.getenv('RENDER_GIT_COMMIT', 'unknown')}`",
            "",
            "### Context",
            "",
            "```json",
            json.dumps(safe_context, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "### Traceback",
            "",
            "```text",
            traceback_text,
            "```",
        ]
    )


def _create_issue(
    repository: str,
    token: str,
    title: str,
    body: str,
    labels: list[str],
) -> str | None:
    payload: dict[str, object] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/issues",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "autodriving-sensor-qa-dashboard",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
            return response_payload.get("html_url")
    except urllib.error.HTTPError as exc:
        response_body = _read_error_response(exc)
        logger.warning("auto_issue_failed status=%s reason=%s body=%s", exc.code, exc.reason, response_body)
    except urllib.error.URLError as exc:
        logger.warning("auto_issue_failed reason=%s", exc.reason)
    except TimeoutError:
        logger.warning("auto_issue_failed reason=timeout")
    except json.JSONDecodeError:
        logger.warning("auto_issue_failed reason=invalid_github_response")

    return None


def _read_error_response(exc: urllib.error.HTTPError) -> str:
    try:
        response_body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    return response_body[:500]


def _redact_text(text: str) -> str:
    redacted = re.sub(r"sensor-qa-upload-[^\s/]+", "sensor-qa-upload-[redacted]", text)
    redacted = re.sub(r"/data/uploads/[^\s]+", "/data/uploads/[redacted]", redacted)
    redacted = re.sub(r"/tmp/[^\s]+", "/tmp/[redacted]", redacted)
    return redacted


def _reset_issue_reporter_state_for_tests() -> None:
    global _issue_count

    with _issue_lock:
        _recent_issue_fingerprints.clear()
        _issue_count = 0
