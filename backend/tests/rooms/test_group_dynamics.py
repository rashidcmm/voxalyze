import pytest

from app.core.config import get_settings
from app.scoring.types import ScorerNotConfigured


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    yield
    get_settings.cache_clear()


def test_raises_when_not_configured(monkeypatch):
    from app.scoring.group_dynamics import score_group_dynamics

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(ScorerNotConfigured):
        score_group_dynamics("transcript", {})


def test_returns_parsed_result_on_success(monkeypatch):
    import app.scoring.group_dynamics as gd

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()

    parsed = gd._GroupDynamicsSchema(
        participants=[
            gd._ParticipantRead(
                participant_id="p1",
                constructiveness=4,
                clarity_of_points=3,
                observations=["built on another participant's point about market sizing"],
            )
        ],
        overall_summary="A focused, evenly-paced discussion.",
    )

    class _FakeMessages:
        def parse(self, **kwargs):
            class _Resp:
                parsed_output = parsed

            return _Resp()

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(gd, "_get_client", lambda: _FakeClient())

    result = gd.score_group_dynamics("p1: hello\np2: hi", {"p1": {"talk_time_pct": 50}})
    assert result["status"] == "ok"
    assert result["participants"][0]["participant_id"] == "p1"
    assert result["overall_summary"].startswith("A focused")


def test_returns_error_status_on_repeated_failure(monkeypatch):
    import app.scoring.group_dynamics as gd

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()

    class _FakeMessages:
        def parse(self, **kwargs):
            raise RuntimeError("api down")

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(gd, "_get_client", lambda: _FakeClient())

    result = gd.score_group_dynamics("transcript", {})
    assert result["status"] == "error"
    assert "api down" in result["error_detail"]
