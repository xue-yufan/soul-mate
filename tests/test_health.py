"""HTTP 健康检查测试"""

# client fixture 见 tests/conftest.py


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["database"]["schema_version"] == "1"


def test_health_version(client):
    resp = client.get("/health")
    body = resp.json()
    assert body["app"] == "soul-mate-backend"
    assert "version" in body
