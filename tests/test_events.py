"""事件信封测试"""

from app.events import make_event, now_iso


def test_make_event_basic():
    event = make_event("pong", version=1)
    assert event["v"] == 1
    assert event["type"] == "pong"
    assert event["request_id"] is None
    assert event["session_id"] is None
    assert event["data"] == {}
    assert "ts" in event


def test_make_event_with_data():
    event = make_event(
        "echo",
        version=2,
        request_id="r1",
        session_id="s1",
        data={"text": "你好"},
    )
    assert event["v"] == 2
    assert event["request_id"] == "r1"
    assert event["session_id"] == "s1"
    assert event["data"] == {"text": "你好"}


def test_now_iso_format():
    ts = now_iso()
    assert "T" in ts
    # 带时区
    assert ts.endswith("+00:00")
