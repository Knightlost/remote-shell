"""
Connect Client (TeamViewer / AnyDesk style)
Connect to any remote computer using only its 6-digit Remote ID and Passcode.
Can launch Claude Chat, Interactive Terminal, or Web Browser.
"""

import sys
import os
import json
import time
import urllib.request
import webbrowser
import subprocess
import traceback
from pathlib import Path

def log_controller_error(err_msg: str):
    log_file = Path(__file__).resolve().parent / "error_controller.log"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] [CONTROLLER ERROR]\n{err_msg}\n{'-'*60}\n")

# Configure console encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def fetch_remote_info(remote_id: str):
    clean_id = remote_id.replace("-", "").strip()
    topic = f"rps_tunnel_{clean_id}"
    url = f"https://ntfy.sh/{topic}/raw?poll=1"

    print(f"[*] Resolving Remote ID: {remote_id}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RPS-Client/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            content = resp.read().decode("utf-8").strip()
            if not content:
                return None
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            if lines:
                return json.loads(lines[-1])
            return None
    except Exception as e:
        print(f"[!] Error fetching remote discovery: {e}")
        return None

def main():
    print("=" * 60)
    print("  🚀 REMOTE POWERSHELL COMMANDER - CONNECT CLIENT")
    print("=" * 60)

    # 1. Get Remote ID
    if len(sys.argv) > 1:
        remote_id = sys.argv[1].strip()
    else:
        remote_id = input("  Enter Remote ID (e.g. 581-932): ").strip()

    if not remote_id:
        print("  [!] Remote ID cannot be empty.")
        return

    # 2. Resolve Remote Info
    data = fetch_remote_info(remote_id)
    if not data or "tunnel_url" not in data:
        print(f"\n  [❌] Could not find host with Remote ID '{remote_id}'.")
        print("  Please make sure the host computer has started 'start_remote.bat'.")
        return

    # 3. Verify Passcode
    expected_pass = str(data.get("passcode", "")).strip()
    user_pass = input(f"  Enter Passcode for [{remote_id}]: ").strip()

    if user_pass != expected_pass:
        print("\n  [❌] Invalid Passcode! Access Denied.")
        return

    tunnel_url = data["tunnel_url"]
    api_key = data["api_key"]

    print("\n" + "=" * 60)
    print(f"  ✅ SUCCESSFULLY CONNECTED TO REMOTE HOST [{remote_id}]")
    print(f"  Endpoint: {tunnel_url}")
    print("=" * 60)

    while True:
        print("\n  Select how you want to control this remote computer:")
        print("  [1] Chat with Claude to control it (Claude Code)")
        print("  [2] Interactive Remote PowerShell Terminal")
        print("  [3] Open Web Dashboard in Browser")
        print("  [4] Exit")

        choice = input("\n  Choose option (1-4) [Default 1]: ").strip() or "1"

        if choice == "1":
            print(f"\n[*] Configuring Claude Code to control Remote PC [{remote_id}]...")
            bridge_script = Path(__file__).resolve().parent / "bridge_mcp.py"
            if not bridge_script.exists():
                bridge_code = '''"""MCP Bridge for Claude Code to execute commands on Remote Computer."""
import sys, json, urllib.request
from typing import Optional
from mcp.server.mcpserver import MCPServer

REMOTE_ID = sys.argv[1] if len(sys.argv) > 1 else "remote"
TUNNEL_URL = sys.argv[2] if len(sys.argv) > 2 else ""
API_KEY = sys.argv[3] if len(sys.argv) > 3 else ""

mcp = MCPServer(
    name=f"remote-pc-{REMOTE_ID}",
    instructions=f"You are controlling the remote Windows computer [{REMOTE_ID}]. All commands run directly on that remote computer via powershell_execute."
)

@mcp.tool(name="powershell_execute", description=f"Execute PowerShell command directly on remote computer [{REMOTE_ID}].")
def powershell_execute(command: str, cwd: Optional[str] = None, timeout: Optional[int] = 60, node_id: Optional[str] = None) -> str:
    url = f"{TUNNEL_URL.rstrip('/')}/api/execute"
    payload = json.dumps({"command": command, "cwd": cwd, "timeout": timeout or 60}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=(timeout or 60) + 15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            out = [f"=== Remote Computer [{REMOTE_ID}] ==="]
            if data.get("stdout"): out.append(f"STDOUT:\\n{data['stdout']}")
            if data.get("stderr"): out.append(f"STDERR:\\n{data['stderr']}")
            out.append(f"Exit Code: {data.get('exit_code', -1)} (Duration: {data.get('duration_ms', 0)}ms)")
            return "\\n".join(out)
    except Exception as e:
        return f"[ERROR] Failed to execute on remote computer [{REMOTE_ID}]: {e}"

@mcp.tool(name="get_system_info", description=f"Get system info from remote computer [{REMOTE_ID}].")
def get_system_info(node_id: Optional[str] = None) -> str:
    url = f"{TUNNEL_URL.rstrip('/')}/api/system-info"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.dumps(json.loads(resp.read().decode("utf-8")), indent=2, ensure_ascii=False)
    except Exception as e:
        return f"[ERROR] Failed to get system info: {e}"

if __name__ == "__main__":
    mcp.run()
'''
                bridge_script.write_text(bridge_code, encoding="utf-8")

            server_name = f"pc-{remote_id}"
            try:
                # Remove any local or stale configurations
                subprocess.run(["claude", "mcp", "remove", server_name, "-s", "local"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                subprocess.run(["claude", "mcp", "remove", server_name, "-s", "user"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                subprocess.run(["claude", "mcp", "remove", "powershell-fleet", "-s", "local"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                subprocess.run(["claude", "mcp", "remove", "powershell-fleet", "-s", "user"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

                # Register robust Stdio Bridge
                subprocess.run(
                    ["claude", "mcp", "add", server_name, sys.executable, str(bridge_script), remote_id, tunnel_url, api_key],
                    check=False
                )
            except FileNotFoundError:
                print("[!] 'claude' command not found. Please ensure Claude Code is installed.")
                continue

            print(f"\n[*] Starting Claude Chat session to control Remote PC [{remote_id}]...")
            print("=" * 60)
            subprocess.run(["claude"], shell=True)
            print("=" * 60)

        elif choice == "2":
            print("\n[*] Interactive Remote PowerShell Terminal")
            print("Type your PowerShell command and press Enter. (Type 'exit' to return)\n")
            while True:
                try:
                    cmd = input(f"PS REMOTE [{remote_id}] > ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not cmd or cmd.lower() in ["exit", "quit"]:
                    break

                try:
                    exec_url = f"{tunnel_url.rstrip('/')}/api/execute"
                    payload = json.dumps({"command": cmd, "timeout": 60}).encode("utf-8")
                    req = urllib.request.Request(
                        exec_url,
                        data=payload,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json"
                        }
                    )
                    with urllib.request.urlopen(req, timeout=65) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        if res.get("stdout"):
                            print(res["stdout"])
                        if res.get("stderr"):
                            print(f"[STDERR]\n{res['stderr']}")
                        print(f"[Exit: {res.get('exit_code')} | {res.get('duration_ms')}ms]\n")
                except Exception as e:
                    print(f"[!] Error executing command: {e}\n")

        elif choice == "3":
            print(f"[*] Opening browser: {tunnel_url}")
            webbrowser.open(tunnel_url)

        elif choice == "4":
            print("Goodbye!")
            break

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Disconnected by user.")
    except Exception as e:
        err_trace = traceback.format_exc()
        print(f"\n[❌] Critical Controller Error: {e}")
        print("[!] Error details written to error_controller.log")
        log_controller_error(err_trace)
        sys.exit(1)
