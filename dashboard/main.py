from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import subprocess
import json

app = FastAPI()
templates = Jinja2Templates(directory="templates")

SERVICES_FILE = "services.json"


def load_services():
    with open(SERVICES_FILE) as f:
        return json.load(f)


def service_status(name):
    r = subprocess.run(
        ["systemctl", "is-active", name],
        capture_output=True,
        text=True
    )
    return r.stdout.strip()


def service_action(name, action):
    subprocess.run(
        ["sudo", "systemctl", action, name],
        capture_output=True
    )


def service_logs(name, lines=50):
    r = subprocess.run(
        ["journalctl", "-u", name, "-n", str(lines), "--no-pager"],
        capture_output=True,
        text=True
    )
    return r.stdout


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    services = load_services()
    for s in services:
        s["status"] = service_status(s["service"])
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "services": services}
    )


@app.get("/status")
def status():
    services = load_services()
    for s in services:
        s["status"] = service_status(s["service"])
    return services


@app.post("/service/{service}/{action}")
def control_service(service: str, action: str):
    if action not in ["start", "stop", "restart"]:
        return JSONResponse({"error": "acción inválida"}, status_code=400)
    service_action(service, action)
    return {"ok": True}


@app.get("/logs/{service}")
def logs(service: str):
    return {"logs": service_logs(service)}


@app.post("/power/reboot")
def reboot():
    subprocess.Popen(["sudo", "systemctl", "reboot"])
    return {"ok": True}


@app.post("/power/shutdown")
def shutdown():
    subprocess.Popen(["sudo", "systemctl", "poweroff"])
    return {"ok": True}

