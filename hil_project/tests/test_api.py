from fastapi.testclient import TestClient

from app.config import Settings, TcpConfig
from app.main import create_app


def test_health_and_state_api():
    app = create_app(Settings(tcp=TcpConfig(enabled=False)))

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        response = client.patch("/api/v1/state", json={"source": "api", "signals": {"Throttle": 55}})
        assert response.status_code == 200
        assert response.json()["signals"]["Throttle"] == 55

        state = client.get("/api/v1/state")
        assert state.status_code == 200
        assert state.json()["signals"]["Throttle"] == 55


def test_gui_and_signal_metadata_routes():
    app = create_app(Settings(tcp=TcpConfig(enabled=False)))

    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "HIL 接口平台" in page.text

        script = client.get("/static/app.js")
        assert script.status_code == 200
        assert "connect" in script.text

        response = client.get("/api/v1/signals")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 44
        assert "档位" in payload["groups"]
        assert "故障状态" in payload["groups"]
        assert any(signal["name"] == "S2-N" and signal["description"] == "空挡" for signal in payload["signals"])


def test_api_rejects_invalid_signal():
    app = create_app(Settings(tcp=TcpConfig(enabled=False)))

    with TestClient(app) as client:
        response = client.patch("/api/v1/state", json={"source": "api", "signals": {"Bad": 1}})
        assert response.status_code == 400


def test_websocket_gets_snapshot():
    app = create_app(Settings(tcp=TcpConfig(enabled=False)))

    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/state") as websocket:
            message = websocket.receive_json()
            assert message["type"] == "state.snapshot"
            assert "Throttle" in message["payload"]["signals"]


def test_websocket_receives_state_update():
    app = create_app(Settings(tcp=TcpConfig(enabled=False)))

    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/state") as websocket:
            assert websocket.receive_json()["type"] == "state.snapshot"
            response = client.patch("/api/v1/state", json={"source": "api", "signals": {"S2-N": 1}})
            assert response.status_code == 200
            message = websocket.receive_json()
            assert message["type"] == "state.updated"
            assert message["payload"]["signals"] == {"S2-N": 1}
