import json

from app.services import issue_reporter


class FakeGitHubResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return b'{"html_url": "https://github.com/Donok53/autodriving_dataset_QA_dashboard/issues/1"}'


def test_issue_reporter_is_disabled_by_default(monkeypatch):
    issue_reporter._reset_issue_reporter_state_for_tests()
    monkeypatch.delenv("AUTO_CREATE_GITHUB_ISSUES", raising=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("GitHub API should not be called when auto issue creation is disabled")

    monkeypatch.setattr(issue_reporter.urllib.request, "urlopen", fail_if_called)

    issue_url = issue_reporter.report_unexpected_error(RuntimeError("boom"), {"stage": "test"})

    assert issue_url is None


def test_issue_reporter_posts_redacted_github_issue(monkeypatch):
    issue_reporter._reset_issue_reporter_state_for_tests()
    monkeypatch.setenv("AUTO_CREATE_GITHUB_ISSUES", "true")
    monkeypatch.setenv("GITHUB_ISSUE_REPOSITORY", "Donok53/autodriving_dataset_QA_dashboard")
    monkeypatch.setenv("GITHUB_ISSUE_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_ISSUE_LABELS", "bug")

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeGitHubResponse()

    monkeypatch.setattr(issue_reporter.urllib.request, "urlopen", fake_urlopen)

    issue_url = issue_reporter.report_unexpected_error(
        RuntimeError("failed at /data/uploads/sensor-qa-upload-private.bag"),
        {"stage": "analysis_job", "filename": "private.bag"},
    )

    assert issue_url == "https://github.com/Donok53/autodriving_dataset_QA_dashboard/issues/1"
    assert captured["url"] == "https://api.github.com/repos/Donok53/autodriving_dataset_QA_dashboard/issues"
    assert captured["timeout"] == 5
    assert captured["payload"]["labels"] == ["bug"]
    assert "private.bag" not in captured["payload"]["body"]
    assert "sensor-qa-upload-private.bag" not in captured["payload"]["body"]
    assert "/data/uploads/[redacted]" in captured["payload"]["body"]


def test_issue_reporter_skips_duplicate_error_within_cooldown(monkeypatch):
    issue_reporter._reset_issue_reporter_state_for_tests()
    monkeypatch.setenv("AUTO_CREATE_GITHUB_ISSUES", "true")
    monkeypatch.setenv("GITHUB_ISSUE_REPOSITORY", "Donok53/autodriving_dataset_QA_dashboard")
    monkeypatch.setenv("GITHUB_ISSUE_TOKEN", "secret-token")

    call_count = 0

    def fake_urlopen(request, timeout):
        nonlocal call_count
        call_count += 1
        return FakeGitHubResponse()

    monkeypatch.setattr(issue_reporter.urllib.request, "urlopen", fake_urlopen)

    first_issue_url = issue_reporter.report_unexpected_error(RuntimeError("same error"), {"stage": "analysis_job"})
    second_issue_url = issue_reporter.report_unexpected_error(RuntimeError("same error"), {"stage": "analysis_job"})

    assert first_issue_url == "https://github.com/Donok53/autodriving_dataset_QA_dashboard/issues/1"
    assert second_issue_url is None
    assert call_count == 1
