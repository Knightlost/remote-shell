"""
Remote Host Service (AnyDesk / TeamViewer style with Remote ID)
Generates a short 6-digit Remote ID and 4-digit Passcode.
Sets up Cloudflare Tunnel and publishes discovery for instant connection from any PC.
"""

import os
import sys
import re
import time
import json
import secrets
import random
import subprocess
import threading
import urllib.request
import urllib.parse
import traceback
from pathlib import Path

def log_host_error(err_msg: str):
    log_file = Path(__file__).resolve().parent / "error_host.log"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] [HOST ERROR]\n{err_msg}\n{'-'*60}\n")

# Configure console encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config import settings, ensure_api_key

import shutil

def get_cloudflared_bin():
    local_cf = os.path.join(os.path.dirname(__file__), "cloudflared.exe")
    if os.path.exists(local_cf):
        return local_cf
    found = shutil.which("cloudflared")
    if found:
        return found
    candidates = [
        local_cf,
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "cloudflared"

CLOUDFLARED_BIN = get_cloudflared_bin()

# Generate or read persistent Remote ID
ID_FILE = settings.BASE_DIR / ".remote_id"

def get_remote_credentials():
    api_key = ensure_api_key()
    if ID_FILE.exists():
        try:
            saved = json.loads(ID_FILE.read_text(encoding="utf-8"))
            return saved["remote_id"], saved["passcode"], api_key
        except Exception:
            pass

    # Generate 6-digit formatted ID (e.g. 582-194) and 4-digit passcode
    part1 = f"{random.randint(100, 999)}"
    part2 = f"{random.randint(100, 999)}"
    remote_id = f"{part1}-{part2}"
    passcode = f"{random.randint(1000, 9999)}"

    ID_FILE.write_text(json.dumps({
        "remote_id": remote_id,
        "passcode": passcode
    }, indent=2), encoding="utf-8")

    return remote_id, passcode, api_key

def publish_discovery(remote_id: str, passcode: str, tunnel_url: str, api_key: str):
    clean_id = remote_id.replace("-", "").strip()
    topic = f"rps_tunnel_{clean_id}"
    payload = json.dumps({
        "remote_id": remote_id,
        "passcode": passcode,
        "tunnel_url": tunnel_url,
        "api_key": api_key,
        "updated_at": time.time()
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=payload,
            headers={"Title": f"RPS Discovery for {remote_id}"}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[!] Signaling update error: {e}")

def free_port_if_needed(port: int):
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                print(f"  [*] Port {port} is in use by a stale process. Cleaning up...")
                if sys.platform == "win32":
                    subprocess.run(
                        f'powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"',
                        shell=True,
                        timeout=5
                    )
                time.sleep(1)
    except Exception:
        pass

def start_server_background():
    free_port_if_needed(settings.PORT)
    import uvicorn
    from server import app
    config = uvicorn.Config(app, host=settings.HOST, port=settings.PORT, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    return t, server

def main():
    remote_id, passcode, api_key = get_remote_credentials()

    print("=" * 62)
    print("  🚀 REMOTE POWERSHELL COMMANDER (REMOTE ID MODE)")
    print("=" * 62)
    print("  Starting background execution engine...")
    t, server = start_server_background()
    time.sleep(1)

    print("  Connecting to Cloudflare Global Edge Network...")
    
    # Launch Cloudflare Tunnel
    proc = subprocess.Popen(
        [CLOUDFLARED_BIN, "tunnel", "--url", f"http://localhost:{settings.PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    tunnel_url = None
    for line in proc.stdout:
        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if match:
            tunnel_url = match.group(0)
            break

    if not tunnel_url:
        print("[!] Could not obtain Cloudflare Tunnel URL.")
        sys.exit(1)

    # Publish discovery
    publish_discovery(remote_id, passcode, tunnel_url, api_key)

    # Background heartbeat publisher
    def heartbeat_loop():
        while True:
            time.sleep(30)
            publish_discovery(remote_id, passcode, tunnel_url, api_key)
            
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()

    print("\n" + "=" * 62)
    print("  ✅ THIS COMPUTER IS NOW READY FOR REMOTE CONTROL")
    print("=" * 62)
    print(f"  👉  YOUR REMOTE ID :  {remote_id}")
    print(f"  🔑  PASSCODE       :  {passcode}")
    print("=" * 62)
    print("  [HOW TO CONNECT FROM ANY OTHER COMPUTER / LAPTOP]:")
    print("  Simply open PowerShell on the other computer and run:")
    print(f"     python connect.py {remote_id}")
    print("     (or double click connect.bat and type this ID)")
    print("=" * 62)
    print(f"  Web Dashboard: {tunnel_url}")
    print("=" * 62)
    print("  Press Ctrl+C to stop remote session.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Remote Host...")
        server.should_exit = True
        proc.terminate()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Remote host stopped by user.")
    except Exception as e:
        err_trace = traceback.format_exc()
        print(f"\n[❌] Critical Error occurred: {e}")
        print("[!] Full details written to error_host.log")
        log_host_error(err_trace)
        sys.exit(1)
