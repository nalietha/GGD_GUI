#!/usr/bin/env python3
import os
import json
import subprocess
import sys

# Configurable paths
SAVE_JSON = "/var/www/ggd_display/wwwroot/data/save.json"
APPSETTINGS_JSON = "/var/www/ggd_display/appsettings.json"

# Services to check (systemd names)
SERVICES = [
    "ggd_display.service",
    "led_listener.service"
]

def check_file(path, name):
    if not os.path.exists(path):
        print(f"[ERROR] {name} is missing: {path}")
        return False
    if os.path.getsize(path) == 0:
        print(f"[ERROR] {name} is empty: {path}")
        return False
    try:
        with open(path, "r") as f:
            json.load(f)
    except Exception as e:
        print(f"[ERROR] {name} is not valid JSON: {e}")
        return False
    print(f"[OK] {name} is present and valid")
    return True

def check_service(service):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            check=False
        )
        if result.returncode == 0:
            print(f"[OK] Service running: {service}")
            return True
        else:
            print(f"[ERROR] Service not running: {service}")
            return False
    except FileNotFoundError:
        print("[WARN] systemctl not found, skipping service checks")
        return False

def restart_services(services):
    for svc in services:
        print(f"Restarting {svc}...")
        subprocess.run(["sudo", "systemctl", "restart", svc])

def main():
    ok = True
    ok &= check_file(SAVE_JSON, "save.json")
    ok &= check_file(APPSETTINGS_JSON, "appsettings.json")
    for svc in SERVICES:
        ok &= check_service(svc)

    if not ok:
        sys.exit(1)

    print("[ALL GOOD] All checks passed ✅")

    # Ask user if they want to restart services
    answer = input("Do you want to restart the services? [y/N]: ").strip().lower()
    if answer in ("y", "yes"):
        restart_services(SERVICES)
        print("[DONE] Services restarted")
    else:
        print("[SKIP] Services were not restarted")

if __name__ == "__main__":
    main()
