"""
MCP (Model Context Protocol) Server for Remote PowerShell Commander.
Supports Zero Trust Multi-Node Fleet Management.
Allows Claude Desktop, Cursor, Zed, Windsurf, or any MCP client to control local and remote computers.
"""
import asyncio
from typing import Optional, List
import json
from mcp.server.mcpserver import MCPServer
from executor import PowerShellExecutor
from node_registry import node_registry

# Initialize MCP Server
mcp = MCPServer(
    name="remote-powershell-commander",
    instructions="""You have direct PowerShell control of this Windows computer.
- When asked to perform actions (create/delete/modify files, desktop shortcuts, check specs, inspect processes, run commands), immediately use the `powershell_execute` tool.
- This computer is already connected, enrolled, and fully online. No additional setup or enrollment is required.
- You can pass node_id='local' or the computer's ID (e.g. pc-XXX-XXX); it executes directly on this computer."""
)

@mcp.tool(
    name="list_nodes",
    description="List connected computers available for control (this computer is online and ready)."
)
def list_nodes() -> str:
    """Returns a list of all active nodes in the fleet."""
    nodes = node_registry.list_nodes()
    result = [
        {
            "node_id": n.node_id,
            "name": n.name,
            "base_url": n.base_url,
            "tags": n.tags,
            "is_active": n.is_active
        }
        for n in nodes
    ]
    return json.dumps(result, indent=2, ensure_ascii=False)

@mcp.tool(
    name="powershell_execute",
    description="Execute any PowerShell command directly on this computer (e.g. creating files on Desktop, managing folders, checking status). Ready immediately without any enrollment."
)
def powershell_execute(command: str, node_id: Optional[str] = "local", cwd: Optional[str] = None, timeout: Optional[int] = 60) -> str:
    """Executes a PowerShell command on the specified node and returns the output."""
    target_node = node_id or "local"
    # Run async function in sync MCP handler
    res = asyncio.run(node_registry.execute_on_node(
        node_id=target_node,
        command=command,
        cwd=cwd,
        timeout=timeout
    ))

    output = [f"=== Node: {res.get('node_name', target_node)} ({target_node}) ==="]
    if res.get("stdout"):
        output.append(f"STDOUT:\n{res['stdout']}")
    if res.get("stderr"):
        output.append(f"STDERR:\n{res['stderr']}")
    output.append(f"Exit Code: {res.get('exit_code', -1)} (Duration: {res.get('duration_ms', 0)}ms)")
    return "\n".join(output)

@mcp.tool(
    name="broadcast_command",
    description="Broadcast and execute a PowerShell command across ALL registered computers (or filtered by tag)."
)
def broadcast_command(command: str, tag: Optional[str] = None, timeout: Optional[int] = 60) -> str:
    """Broadcasts command to multiple nodes."""
    nodes = node_registry.list_nodes()
    if tag:
        nodes = [n for n in nodes if tag in n.tags]
    
    if not nodes:
        return f"No nodes found matching tag: {tag}"

    async def run_all():
        tasks = [
            node_registry.execute_on_node(node_id=n.node_id, command=command, timeout=timeout)
            for n in nodes
        ]
        return await asyncio.gather(*tasks)

    results = asyncio.run(run_all())
    formatted = []
    for r in results:
        formatted.append(f"=== Node: {r.get('node_name', r.get('node_id'))} ===")
        if r.get("stdout"):
            formatted.append(f"STDOUT:\n{r['stdout']}")
        if r.get("stderr"):
            formatted.append(f"STDERR:\n{r['stderr']}")
        formatted.append(f"Exit Code: {r.get('exit_code')} ({r.get('duration_ms')}ms)\n")

    return "\n".join(formatted)

@mcp.tool(
    name="get_system_info",
    description="Get hardware, CPU/RAM, and disk diagnostic info for a computer (default is 'local')."
)
def get_system_info(node_id: Optional[str] = "local") -> str:
    """Returns system diagnostic information as JSON formatted string."""
    target_node = node_id or "local"
    info = asyncio.run(node_registry.get_node_sysinfo(target_node))
    return json.dumps(info, indent=2, ensure_ascii=False)

@mcp.tool(
    name="read_audit_logs",
    description="Read recent command execution history and audit log from this host computer."
)
def read_audit_logs(limit: int = 20) -> str:
    """Returns recent audit logs."""
    logs = PowerShellExecutor.get_recent_audit_logs(limit=limit)
    return json.dumps(logs, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    mcp.run()
