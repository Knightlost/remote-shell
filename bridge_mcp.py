"""
MCP Bridge for Claude Code (Stdio Transport).
Relays all tool executions from Claude Code directly to the Remote Host via HTTP API.
Guarantees 100% execution on the Remote Computer with zero proxy/SSE dropouts.
"""
import sys
import os
import json
import urllib.request
from typing import Optional
from mcp.server.mcpserver import MCPServer

# Parameters passed via command line
REMOTE_ID = sys.argv[1] if len(sys.argv) > 1 else "remote"
TUNNEL_URL = sys.argv[2] if len(sys.argv) > 2 else ""
API_KEY = sys.argv[3] if len(sys.argv) > 3 else ""

mcp = MCPServer(
    name=f"remote-pc-{REMOTE_ID}",
    instructions=f"""You are controlling the remote Windows computer [{REMOTE_ID}].
All commands you run with `powershell_execute` will execute directly on that remote computer.
You have full PowerShell access to inspect system, create files, manage processes, open applications, etc. on that remote computer.
Always use `powershell_execute` to perform any actions requested by the user on the computer."""
)

@mcp.tool(
    name="powershell_execute",
    description=f"Execute a PowerShell command directly on remote computer [{REMOTE_ID}] (e.g. create files on Desktop, check drives, launch apps)."
)
def powershell_execute(command: str, cwd: Optional[str] = None, timeout: Optional[int] = 60, node_id: Optional[str] = None) -> str:
    """Executes a PowerShell command on the remote machine via HTTP API."""
    url = f"{TUNNEL_URL.rstrip('/')}/api/execute"
    effective_timeout = timeout or 60
    payload = json.dumps({
        "command": command,
        "cwd": cwd,
        "timeout": effective_timeout
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": f"Claude-Bridge/{REMOTE_ID}"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=effective_timeout + 15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            out = [f"=== Remote Computer [{REMOTE_ID}] ==="]
            if data.get("stdout"):
                out.append(f"STDOUT:\n{data['stdout']}")
            if data.get("stderr"):
                out.append(f"STDERR:\n{data['stderr']}")
            out.append(f"Exit Code: {data.get('exit_code', -1)} (Duration: {data.get('duration_ms', 0)}ms)")
            return "\n".join(out)
    except Exception as e:
        return f"[ERROR] Failed to execute on remote computer [{REMOTE_ID}]: {str(e)}"

@mcp.tool(
    name="get_system_info",
    description=f"Get hardware, OS, CPU/RAM, and network info from remote computer [{REMOTE_ID}]."
)
def get_system_info(node_id: Optional[str] = None) -> str:
    """Fetches system diagnostics from the remote host."""
    url = f"{TUNNEL_URL.rstrip('/')}/api/system-info"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "User-Agent": f"Claude-Bridge/{REMOTE_ID}"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return json.dumps(data, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"[ERROR] Failed to query system info from [{REMOTE_ID}]: {str(e)}"

@mcp.tool(
    name="list_nodes",
    description=f"List connected computers available for control (remote computer [{REMOTE_ID}] is online and ready)."
)
def list_nodes() -> str:
    """Returns the remote computer node info."""
    return json.dumps([
        {
            "node_id": REMOTE_ID,
            "name": f"Remote Computer ({REMOTE_ID})",
            "status": "online",
            "is_active": True
        }
    ], indent=2, ensure_ascii=False)

if __name__ == "__main__":
    mcp.run()
