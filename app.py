from __future__ import annotations

import os
import atexit
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from brand_monitor.api_collector import ApiConfigStore, OfficialApiCollector
from brand_monitor.db import initialize
from brand_monitor.scheduler import Scheduler
from brand_monitor.service import MonitorService
from brand_monitor.webdriver_collector import WebDriverManager


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("BRAND_MONITOR_DB", str(BASE_DIR / "instance" / "monitor.db"))

app = Flask(__name__)
app.json.ensure_ascii = False
initialize(DB_PATH, datetime.now().astimezone().isoformat(timespec="seconds"))
webdriver_manager = WebDriverManager(BASE_DIR / "instance" / "browser_profiles")
api_config_store = ApiConfigStore(
    os.environ.get("BRAND_MONITOR_API_CONFIG", str(BASE_DIR / "instance" / "api_config.json"))
)
api_collector = OfficialApiCollector(api_config_store)
atexit.register(webdriver_manager.close_all)
service = MonitorService(DB_PATH, webdriver_manager=webdriver_manager, api_collector=api_collector)
scheduler = Scheduler(service)
scheduler.start()


def ok(data=None, **extra):
    return jsonify({"ok": True, "data": data, **extra})


def error_response(exc, status=400):
    return jsonify({"ok": False, "error": str(exc)}), status


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def get_config():
    config = service.config()
    return ok(config, scheduler=scheduler.status(), browsers=webdriver_manager.statuses(config["platforms"]),
              apis=api_collector.statuses(config["platforms"]), api_config=api_collector.public_config())


@app.put("/api/api-config")
def put_api_config():
    try:
        data = request.get_json(force=True)
        api_config_store.update(data.get("values", {}), data.get("clear", []))
        return ok({"statuses": api_collector.statuses(service.platforms()),
                   "config": api_collector.public_config()})
    except Exception as exc:
        return error_response(exc)


@app.put("/api/settings")
def put_settings():
    try:
        scheduler.next_run = None
        return ok(service.update_settings(request.get_json(force=True)))
    except Exception as exc:
        return error_response(exc)


@app.post("/api/prompts")
def post_prompt():
    try:
        return ok(service.add_prompt(request.get_json(force=True).get("text", "")))
    except Exception as exc:
        return error_response(exc)


@app.patch("/api/<kind>/<int:item_id>")
def patch_config(kind, item_id):
    try:
        service.toggle(kind, item_id, bool(request.get_json(force=True).get("active")))
        return ok()
    except Exception as exc:
        return error_response(exc)


@app.delete("/api/prompts/<int:item_id>")
def delete_prompt(item_id):
    service.delete_prompt(item_id)
    return ok()


@app.post("/api/runs")
def post_run():
    try:
        data = request.get_json(force=True)
        if data.get("all"):
            return ok({"run_ids": service.run_all(data.get("mode", "demo"))})
        return ok({"run_id": service.run(int(data["prompt_id"]), data.get("mode", "demo"))})
    except Exception as exc:
        return error_response(exc)


@app.post("/api/manual-capture")
def post_manual_capture():
    try:
        return ok({"run_id": service.manual_capture(request.get_json(force=True))})
    except Exception as exc:
        return error_response(exc)


@app.get("/api/browser/status")
def browser_status():
    return ok(webdriver_manager.statuses(service.platforms()))


@app.post("/api/browser/login")
def browser_login():
    try:
        platform = service.platform(int(request.get_json(force=True)["platform_id"]))
        return ok(webdriver_manager.open_login(platform))
    except Exception as exc:
        return error_response(exc)


@app.post("/api/browser/check")
def browser_check():
    try:
        platform = service.platform(int(request.get_json(force=True)["platform_id"]))
        return ok(webdriver_manager.status(platform))
    except Exception as exc:
        return error_response(exc)


@app.post("/api/browser/close")
def browser_close():
    try:
        platform = service.platform(int(request.get_json(force=True)["platform_id"]))
        webdriver_manager.close(platform["slug"])
        return ok()
    except Exception as exc:
        return error_response(exc)


@app.post("/api/browser/source-diagnostics")
def browser_source_diagnostics():
    try:
        platform = service.platform(int(request.get_json(force=True)["platform_id"]))
        return ok(webdriver_manager.source_diagnostics(platform))
    except Exception as exc:
        return error_response(exc)


@app.get("/api/dashboard")
def get_dashboard():
    try:
        days = min(365, max(0, int(request.args.get("days", "30"))))
        return ok(service.dashboard(days=days))
    except Exception as exc:
        return error_response(exc)


@app.get("/api/export.csv")
def export_csv():
    return Response(
        service.export_csv(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=brand-exposure.csv"},
    )


@app.get("/api/export-retrieval.csv")
def export_retrieval_csv():
    return Response(
        service.export_retrieval_csv(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=retrieval-funnel.csv"},
    )


@app.get("/health")
def health():
    return ok({"status": "healthy", "scheduler": scheduler.status()})


@app.get("/favicon.ico")
def favicon():
    return Response(status=204)


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
