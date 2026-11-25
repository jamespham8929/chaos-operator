"""Tests for the run records other tools read from status."""

from datetime import datetime, timezone

from chaos_operator import history

STARTED = datetime(2026, 11, 24, 3, 0, 5, 123456, tzinfo=timezone.utc)
FINISHED = datetime(2026, 11, 24, 3, 5, 9, tzinfo=timezone.utc)


def record(spec=None, result=None, **kwargs):
    spec = spec or {"type": "pod-failure", "selector": {"matchLabels": {"app": "checkout"}}}
    return history.run_record(
        spec, kwargs.get("name", "nightly"), kwargs.get("namespace", "payments"),
        "schedule", STARTED, FINISHED,
        {"status": "completed", "pods_deleted": 2} if result is None else result,
    )


class TestServiceFor:
    def test_explicit_service_wins(self):
        spec = {"service": "checkout-api", "selector": {"matchLabels": {"app": "web"}}}
        assert history.service_for(spec) == "checkout-api"

    def test_recommended_label_beats_app(self):
        labels = {"app": "web", "app.kubernetes.io/name": "storefront"}
        assert history.service_for({"selector": {"matchLabels": labels}}) == "storefront"

    def test_falls_back_to_app_label(self):
        assert history.service_for({"selector": {"matchLabels": {"app": "web"}}}) == "web"

    def test_unknown_when_nothing_names_it(self):
        assert history.service_for({"selector": {"matchLabels": {"tier": "api"}}}) == ""
        assert history.service_for({}) == ""


class TestRunRecord:
    def test_fields(self):
        r = record()
        assert r == {
            "id": "payments/nightly/20261124T030005Z",
            "trigger": "schedule",
            "type": "pod-failure",
            "service": "checkout",
            "selector": {"app": "checkout"},
            "startedAt": "2026-11-24T03:00:05Z",
            "finishedAt": "2026-11-24T03:05:09Z",
            "outcome": "completed",
            "injected": True,
            "podsAffected": 2,
        }

    def test_a_run_that_touched_nothing_is_marked(self):
        for outcome in ("no_targets", "skipped", "refused", "unsupported_for_safe_run"):
            assert record(result={"status": outcome})["injected"] is False, outcome

    def test_an_aborted_run_still_injected_a_fault(self):
        # The guard tripped because the fault hurt something. That is exactly
        # the run an incident tool should see.
        r = record(result={"status": "aborted", "blastRadius": 3})
        assert r["injected"] is True
        assert r["outcome"] == "aborted"

    def test_missing_status_is_unknown(self):
        assert record(result={})["outcome"] == "unknown"

    def test_id_is_in_utc(self):
        from datetime import timedelta
        local = STARTED.astimezone(timezone(timedelta(hours=-5)))
        r = history.run_record({}, "x", "ns", "create", local, FINISHED, {"status": "completed"})
        assert r["id"] == "ns/x/20261124T030005Z"


class TestAppend:
    def test_keeps_newest_last(self):
        runs = history.append([{"id": "a"}], {"id": "b"})
        assert [r["id"] for r in runs] == ["a", "b"]

    def test_trims_to_the_limit(self):
        runs = [{"id": str(i)} for i in range(history.HISTORY_LIMIT)]
        runs = history.append(runs, {"id": "new"})
        assert len(runs) == history.HISTORY_LIMIT
        assert runs[0]["id"] == "1"
        assert runs[-1]["id"] == "new"

    def test_does_not_change_the_input(self):
        original = [{"id": "a"}]
        history.append(original, {"id": "b"})
        assert original == [{"id": "a"}]

    def test_starts_from_nothing(self):
        assert history.append(None, {"id": "a"}) == [{"id": "a"}]
