#!/usr/bin/env python

import atexit

from flask import Flask, jsonify, render_template, request

import state_manager


app = Flask(__name__)
app.config["SECRET_KEY"] = "development key"

g_state = state_manager.StateManager()
atexit.register(g_state.close)


@app.get("/")
def vehicle_data():
    return render_template(
        "vehicle_controls.html",
        tcp_port=g_state.tcp_port,
        local_ip=g_state.local_ip,
    )


@app.get("/api/state")
def get_state():
    return jsonify(g_state.snapshot())


@app.post("/api/control")
def set_control():
    """接收动力学及临时反馈 JSON；字段非法时返回 400，供网页或外部客户端修正。"""
    payload = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(g_state.update_controls(payload))
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error)), 400


@app.get("/api/map")
def get_map():
    return jsonify(g_state.map_payload())


@app.get("/api/vehicle-feedback")
def get_vehicle_feedback():
    """返回持久化模式、两路制动气压和实际反馈模式。"""
    return jsonify(g_state.vehicle_feedback_payload())


@app.post("/api/vehicle-feedback")
def set_vehicle_feedback():
    """保存网页反馈配置；非法输入返回 400，存储失败返回 500 且保留原设置。"""
    try:
        return jsonify(g_state.update_vehicle_feedback(request.get_json(silent=True)))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except OSError:
        app.logger.exception("车辆反馈配置保存失败")
        return jsonify(error="保存失败，请检查配置目录权限或磁盘空间"), 500


@app.get("/api/map/export")
def export_map():
    return jsonify(g_state.map_payload())


@app.post("/api/map/import")
def import_map():
    payload = request.get_json(force=True, silent=True) or {}
    return jsonify(g_state.import_map(payload))


@app.post("/api/map/settings")
def update_map_settings():
    payload = request.get_json(force=True, silent=True) or {}
    return jsonify(g_state.update_map_settings(payload))


@app.post("/api/map/objects")
def add_map_object():
    payload = request.get_json(force=True, silent=True) or {}
    return jsonify(g_state.add_map_object(payload))


@app.put("/api/map/objects/<object_id>")
def update_map_object(object_id):
    payload = request.get_json(force=True, silent=True) or {}
    return jsonify(g_state.update_map_object(object_id, payload))


@app.delete("/api/map/objects/<object_id>")
def delete_map_object(object_id):
    return jsonify(g_state.delete_map_object(object_id))


@app.post("/api/map/reset")
def reset_map():
    return jsonify(g_state.reset_map())


@app.post("/api/elevation/brush")
def brush_elevation():
    payload = request.get_json(force=True, silent=True) or {}
    return jsonify(g_state.brush_elevation(payload))


@app.get("/api/response-config")
def get_response_config():
    return jsonify(g_state.response_config())


@app.post("/api/response-config")
def update_response_config():
    payload = request.get_json(force=True, silent=True) or {}
    return jsonify(g_state.update_response_config(payload))


@app.post("/api/pause")
def pause():
    payload = request.get_json(force=True, silent=True) or {}
    paused = bool(payload.get("paused", False))
    return jsonify(g_state.set_paused(paused))


@app.get("/api/hil")
def get_hil_state():
    return jsonify(g_state.snapshot()["hil"])


@app.get("/api/vehicle-protocol")
def get_vehicle_protocol():
    return jsonify(g_state.vehicle_protocol_payload())


if __name__ == "__main__":
    flask_port = 50000
    print(f"Web UI: http://localhost:{flask_port}")
    print(f"TCP control/telemetry: {g_state.local_ip}:{g_state.tcp_port}")
    hil = g_state.hil_client.status()
    print(f"HIL TCP client: {hil['host']}:{hil['port']}")
    output = g_state.vehicle_publisher.status()
    print(f"Vehicle protocol output: {output['url']}")
    print(f"DDS bridge enabled: {g_state.dds_bridge.status()['enabled']} (status: /api/state -> dds)")
    app.run(use_reloader=False, host="0.0.0.0", port=flask_port, threaded=True)
