"""WebSocket 协议测试"""

# client fixture 见 tests/conftest.py


def test_ping_pong(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"v": 1, "type": "ping", "request_id": "r1", "data": {}})
        resp = ws.receive_json()
        assert resp["type"] == "pong"
        assert resp["v"] == 1
        assert resp["request_id"] == "r1"
        assert "server_time" in resp["data"]


def test_echo(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"v": 1, "type": "echo", "request_id": "r2", "data": {"text": "你好"}})
        resp = ws.receive_json()
        assert resp["type"] == "echo"
        assert resp["data"]["text"] == "你好"


def test_bad_json(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text("not json")
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "bad_json"


def test_missing_fields(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ping"})
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "bad_request"


def test_version_mismatch(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"v": 999, "type": "ping", "request_id": "r3", "data": {}})
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "version_mismatch"
        assert "999" in resp["data"]["message"]


def test_unsupported_type(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"v": 1, "type": "foo", "request_id": "r4", "data": {}})
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "unsupported_type"
