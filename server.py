import os
import sys
import shutil
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, Security, status, Header, Query, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field

# Ensure UTF-8 output encoding in Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import settings, ensure_api_key
from executor import PowerShellExecutor
from node_registry import node_registry, NodeInfo
from mcp_server import mcp
from mcp.server.transport_security import TransportSecuritySettings

# Initialize FastAPI app with OpenAPI metadata
app = FastAPI(
    title="Remote PowerShell Commander & Zero Trust Fleet Manager",
    description="Secure Remote PowerShell Execution and Multi-Node Zero Trust Fleet Manager designed for AI Agents (ChatGPT, Claude, Cursor, Custom Agents).",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Mount MCP Server-Sent Events (SSE) application for remote Claude connections
app.mount("/mcp", mcp.sse_app(transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)))

# Enable CORS for external AI agents and web dashboards
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security_bearer = HTTPBearer(auto_error=False)

# Authentication dependency
async def verify_auth(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    token: Optional[str] = Query(None)
):
    current_key = settings.REMOTE_SHELL_API_KEY or ensure_api_key()
    
    provided_token = None
    if auth and auth.credentials:
        provided_token = auth.credentials
    elif x_api_key:
        provided_token = x_api_key
    elif token:
        provided_token = token

    if not provided_token or provided_token != current_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key. Provide via Bearer token, 'X-API-Key' header, or '?token=' query parameter.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True

# Request and Response Models
class ExecuteRequest(BaseModel):
    command: str = Field(..., description="PowerShell command or script to execute on the host machine.")
    cwd: Optional[str] = Field(None, description="Optional working directory for the command execution.")
    timeout: Optional[int] = Field(None, description="Command execution timeout in seconds (max 300).")
    node_id: Optional[str] = Field("local", description="Target computer ID (e.g. 'local', or remote node ID).")

class BroadcastRequest(BaseModel):
    command: str = Field(..., description="Command to broadcast across all nodes.")
    tag: Optional[str] = Field(None, description="Optional tag filter (e.g. 'windows', 'servers').")
    timeout: Optional[int] = Field(60, description="Execution timeout.")

class RegisterNodeRequest(BaseModel):
    node_id: str = Field(..., description="Unique identifier for the remote machine (e.g. 'laptop-office').")
    name: str = Field(..., description="Human readable name.")
    base_url: str = Field(..., description="Reachable URL/IP (e.g. 'http://100.x.y.z:8080' via Tailscale).")
    api_key: str = Field(..., description="API key to authenticate with that remote node.")
    tags: List[str] = Field(default_factory=list, description="Tags like ['windows', 'dev'].")

# System & Execution Endpoints
@app.get("/api/health", tags=["System"])
async def health_check():
    """Public healthcheck endpoint."""
    return {"status": "online", "service": "Remote PowerShell Commander & Zero Trust Fleet"}

@app.post("/api/execute", tags=["Execution"], dependencies=[Depends(verify_auth)])
async def execute_command(req: ExecuteRequest):
    """
    Execute a PowerShell command on the target computer (local host or remote node).
    """
    target = req.node_id or "local"
    return await node_registry.execute_on_node(
        node_id=target,
        command=req.command,
        cwd=req.cwd,
        timeout=req.timeout
    )

@app.get("/api/system-info", tags=["System"], dependencies=[Depends(verify_auth)])
async def get_system_info(node_id: Optional[str] = Query("local")):
    """
    Get hardware and system metrics for local PC or a specific node.
    """
    target = node_id or "local"
    return await node_registry.get_node_sysinfo(target)

@app.get("/api/logs", tags=["Audit"], dependencies=[Depends(verify_auth)])
async def get_audit_logs(limit: int = Query(50, ge=1, le=500)):
    """
    Get recent command execution audit logs from this host.
    """
    return PowerShellExecutor.get_recent_audit_logs(limit=limit)

# Fleet Management Endpoints
@app.get("/api/nodes", tags=["Fleet Management"], dependencies=[Depends(verify_auth)])
async def list_fleet_nodes():
    """List all registered nodes in the Zero Trust fleet."""
    return node_registry.list_nodes()

@app.post("/api/nodes/register", tags=["Fleet Management"], dependencies=[Depends(verify_auth)])
async def register_fleet_node(req: RegisterNodeRequest):
    """Enroll a new remote computer node into the fleet."""
    node = NodeInfo(
        node_id=req.node_id,
        name=req.name,
        base_url=req.base_url,
        api_key=req.api_key,
        tags=req.tags,
        is_active=True
    )
    saved = node_registry.register_node(node)
    return {"success": True, "message": f"Node '{node.name}' enrolled.", "node": saved}

@app.delete("/api/nodes/{node_id}", tags=["Fleet Management"], dependencies=[Depends(verify_auth)])
async def delete_fleet_node(node_id: str):
    """Remove a node from the fleet."""
    if node_id == "local":
        raise HTTPException(status_code=400, detail="Cannot delete local host node.")
    removed = node_registry.remove_node(node_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Node not found.")
    return {"success": True, "message": f"Node '{node_id}' removed."}

@app.post("/api/nodes/broadcast", tags=["Fleet Management"], dependencies=[Depends(verify_auth)])
async def broadcast_command(req: BroadcastRequest):
    """Broadcast command execution to multiple nodes in parallel."""
    nodes = node_registry.list_nodes()
    if req.tag:
        nodes = [n for n in nodes if req.tag in n.tags]

    tasks = [
        node_registry.execute_on_node(node_id=n.node_id, command=req.command, timeout=req.timeout)
        for n in nodes
    ]
    results = await asyncio.gather(*tasks)
    return {"total_nodes": len(nodes), "results": results}

# File Upload / Download
@app.post("/api/upload", tags=["Files"], dependencies=[Depends(verify_auth)])
async def upload_file(destination_path: str = Query(..., description="Target file path on host"), file: UploadFile = File(...)):
    """Upload a file to the host system."""
    try:
        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {"success": True, "path": str(dest.resolve()), "size_bytes": dest.stat().st_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.get("/api/download", tags=["Files"], dependencies=[Depends(verify_auth)])
async def download_file(file_path: str = Query(..., description="File path on host to download")):
    """Download a file from the host system."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=str(p.resolve()), filename=p.name)

# Web Dashboard UI
@app.get("/", response_class=HTMLResponse, tags=["UI"])
@app.get("/dashboard", response_class=HTMLResponse, tags=["UI"])
async def dashboard():
    api_key = settings.REMOTE_SHELL_API_KEY
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zero Trust Remote PowerShell Commander</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #0d1117;
            --surface: #161b22;
            --border: #30363d;
            --accent: #58a6ff;
            --accent-green: #3fb950;
            --accent-red: #f85149;
            --accent-yellow: #d29922;
            --accent-purple: #bc8cff;
            --text-main: #c9d1d9;
            --text-muted: #8b949e;
            --terminal-bg: #030712;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg);
            color: var(--text-main);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        header {{
            background: var(--surface);
            border-bottom: 1px solid var(--border);
            padding: 0.9rem 1.5rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 1rem;
        }}
        .brand {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }}
        .brand-badge {{
            background: linear-gradient(135deg, #1f6feb, #8957e5);
            color: white;
            font-weight: 700;
            padding: 0.35rem 0.65rem;
            border-radius: 6px;
            font-size: 0.82rem;
            letter-spacing: 0.5px;
        }}
        .brand h1 {{
            font-size: 1.15rem;
            font-weight: 600;
            color: #f0f6fc;
        }}
        .auth-bar {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}
        .auth-bar input {{
            background: var(--bg);
            border: 1px solid var(--border);
            color: #fff;
            padding: 0.4rem 0.8rem;
            border-radius: 6px;
            font-family: 'Fira Code', monospace;
            font-size: 0.85rem;
            width: 260px;
        }}
        .auth-bar button {{
            background: #238636;
            color: white;
            border: none;
            padding: 0.45rem 0.9rem;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
            transition: 0.2s;
        }}
        .auth-bar button:hover {{ background: #2ea043; }}
        
        .main-container {{
            flex: 1;
            display: grid;
            grid-template-columns: 320px 1fr;
            height: calc(100vh - 65px);
        }}
        @media(max-width: 900px) {{
            .main-container {{ grid-template-columns: 1fr; height: auto; }}
        }}
        
        /* Sidebar */
        aside {{
            background: var(--surface);
            border-right: 1px solid var(--border);
            padding: 1.25rem;
            display: flex;
            flex-direction: column;
            gap: 1.25rem;
            overflow-y: auto;
        }}
        .card {{
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1rem;
        }}
        .card h2 {{
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.7px;
            color: var(--text-muted);
            margin-bottom: 0.75rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .metric-row {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 0.5rem;
            font-size: 0.85rem;
        }}
        .progress-bar {{
            background: #21262d;
            height: 6px;
            border-radius: 3px;
            overflow: hidden;
            margin-top: 0.25rem;
            margin-bottom: 0.75rem;
        }}
        .progress-fill {{
            height: 100%;
            background: var(--accent);
            border-radius: 3px;
            transition: width 0.3s ease;
        }}
        .quick-btn {{
            display: block;
            width: 100%;
            text-align: left;
            background: #21262d;
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 0.45rem 0.7rem;
            border-radius: 6px;
            font-size: 0.78rem;
            cursor: pointer;
            margin-bottom: 0.45rem;
            transition: 0.2s;
            font-family: 'Fira Code', monospace;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .quick-btn:hover {{
            background: #30363d;
            color: var(--accent);
            border-color: var(--accent);
        }}
        
        /* Main Workspace */
        main {{
            display: flex;
            flex-direction: column;
            background: var(--terminal-bg);
            overflow: hidden;
        }}
        .tabs {{
            display: flex;
            background: var(--surface);
            border-bottom: 1px solid var(--border);
            padding: 0 1rem;
        }}
        .tab {{
            padding: 0.75rem 1.25rem;
            color: var(--text-muted);
            cursor: pointer;
            font-weight: 500;
            font-size: 0.88rem;
            border-bottom: 2px solid transparent;
        }}
        .tab.active {{
            color: #fff;
            border-bottom-color: var(--accent);
        }}
        
        .tab-content {{
            flex: 1;
            display: none;
            flex-direction: column;
            overflow: hidden;
            padding: 1.25rem;
        }}
        .tab-content.active {{
            display: flex;
        }}

        /* Terminal Console */
        .target-selector-bar {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-bottom: 0.75rem;
            background: var(--surface);
            padding: 0.5rem 0.75rem;
            border-radius: 6px;
            border: 1px solid var(--border);
            font-size: 0.85rem;
        }}
        .target-selector-bar select {{
            background: var(--bg);
            color: #fff;
            border: 1px solid var(--border);
            padding: 0.35rem 0.75rem;
            border-radius: 4px;
            font-family: 'Inter', sans-serif;
            font-size: 0.85rem;
            outline: none;
        }}
        .terminal-output {{
            flex: 1;
            background: #06090f;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1rem;
            font-family: 'Fira Code', monospace;
            font-size: 0.88rem;
            color: #38bdf8;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
            margin-bottom: 0.75rem;
        }}
        .terminal-input-container {{
            display: flex;
            gap: 0.75rem;
            background: var(--surface);
            padding: 0.75rem;
            border-radius: 8px;
            border: 1px solid var(--border);
        }}
        .terminal-prompt {{
            font-family: 'Fira Code', monospace;
            color: var(--accent-green);
            font-weight: 600;
            display: flex;
            align-items: center;
        }}
        .terminal-input {{
            flex: 1;
            background: transparent;
            border: none;
            outline: none;
            color: #fff;
            font-family: 'Fira Code', monospace;
            font-size: 0.95rem;
        }}
        .run-btn {{
            background: #1f6feb;
            color: white;
            border: none;
            padding: 0.5rem 1.2rem;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
        }}
        .run-btn:hover {{ background: #388bfd; }}
        
        /* Node Fleet Grid */
        .fleet-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 1rem;
            margin-top: 1rem;
            overflow-y: auto;
        }}
        .node-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1rem;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            position: relative;
        }}
        .node-card.online {{ border-left: 4px solid var(--accent-green); }}
        .node-card.remote {{ border-left: 4px solid var(--accent-purple); }}
        .node-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
        }}
        .node-title {{
            font-weight: 600;
            font-size: 1rem;
            color: #fff;
        }}
        .node-badge {{
            font-size: 0.7rem;
            padding: 2px 6px;
            border-radius: 4px;
            background: #21262d;
            color: var(--accent);
            font-family: 'Fira Code', monospace;
        }}
        .node-detail {{
            font-size: 0.8rem;
            color: var(--text-muted);
            word-break: break-all;
        }}
        .node-actions {{
            display: flex;
            gap: 0.5rem;
            margin-top: 0.5rem;
        }}
        .node-btn {{
            background: #21262d;
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 0.35rem 0.65rem;
            border-radius: 4px;
            font-size: 0.75rem;
            cursor: pointer;
        }}
        .node-btn:hover {{ background: #30363d; color: #fff; }}
        .node-btn.danger {{ color: var(--accent-red); }}
        .node-btn.danger:hover {{ background: #b62324; color: #fff; }}

        /* Add Node Form */
        .form-row {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 0.75rem;
            margin-bottom: 0.75rem;
        }}
        .form-group label {{
            display: block;
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-bottom: 0.25rem;
        }}
        .form-group input {{
            width: 100%;
            background: var(--bg);
            border: 1px solid var(--border);
            padding: 0.45rem 0.65rem;
            border-radius: 6px;
            color: #fff;
            font-family: 'Fira Code', monospace;
            font-size: 0.85rem;
        }}

        /* AI Instructions Tab */
        .ai-guide {{
            overflow-y: auto;
            max-width: 900px;
            margin: 0 auto;
            padding-bottom: 2rem;
        }}
        .ai-guide h3 {{ color: #58a6ff; margin: 1.5rem 0 0.5rem 0; font-size: 1.1rem; }}
        .ai-guide p, .ai-guide li {{ color: #8b949e; line-height: 1.6; font-size: 0.95rem; margin-bottom: 0.5rem; }}
        .code-box {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 0.75rem 1rem;
            font-family: 'Fira Code', monospace;
            font-size: 0.85rem;
            color: #7ee787;
            overflow-x: auto;
            position: relative;
            margin: 0.75rem 0;
        }}
    </style>
</head>
<body>
    <header>
        <div class="brand">
            <span class="brand-badge">ZERO TRUST FLEET</span>
            <h1>Remote PowerShell Commander for AI</h1>
        </div>
        <div class="auth-bar">
            <span style="font-size: 0.85rem; color: var(--text-muted);">Master API Key:</span>
            <input type="password" id="apiKeyInput" value="{api_key}" placeholder="Paste API Key here...">
            <button onclick="saveApiKey()">Save Key</button>
            <a href="/docs" target="_blank" style="color: var(--accent); font-size: 0.85rem; text-decoration: none; margin-left: 0.5rem;">OpenAPI Docs ↗</a>
        </div>
    </header>

    <div class="main-container">
        <!-- Sidebar -->
        <aside>
            <div class="card">
                <h2>Host Diagnostics <button onclick="refreshSysInfo()" style="background:none;border:none;color:var(--accent);cursor:pointer;font-size:0.75rem;">↻ Refresh</button></h2>
                <div class="metric-row">
                    <span>Host:</span>
                    <strong id="statHost">-</strong>
                </div>
                <div class="metric-row">
                    <span>OS:</span>
                    <span id="statOs" style="font-size: 0.75rem; color: #8b949e;">-</span>
                </div>
                
                <div class="metric-row" style="margin-top: 0.5rem;">
                    <span>CPU Usage:</span>
                    <span id="statCpu">0%</span>
                </div>
                <div class="progress-bar"><div class="progress-fill" id="barCpu" style="width: 0%;"></div></div>

                <div class="metric-row">
                    <span>RAM Usage:</span>
                    <span id="statRam">0 / 0 GB</span>
                </div>
                <div class="progress-bar"><div class="progress-fill" id="barRam" style="width: 0%;"></div></div>

                <div class="metric-row">
                    <span>Disk C:</span>
                    <span id="statDisk">0%</span>
                </div>
                <div class="progress-bar"><div class="progress-fill" id="barDisk" style="width: 0%;"></div></div>
            </div>

            <div class="card">
                <h2>Quick Commands</h2>
                <button class="quick-btn" onclick="runQuick('Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 Id,ProcessName,CPU')">⚡ Top 10 CPU Processes</button>
                <button class="quick-btn" onclick="runQuick('Get-Service | Where-Object Status -eq \\'Running\\' | Select-Object -First 15 Name,DisplayName')">⚙️ Running Services</button>
                <button class="quick-btn" onclick="runQuick('ipconfig')">🌐 Network Configuration</button>
                <button class="quick-btn" onclick="runQuick('Get-ChildItem -Path . | Select-Object Name,Length,LastWriteTime')">📁 List Current Directory</button>
                <button class="quick-btn" onclick="runQuick('Get-ComputerInfo | Select-Object WindowsProductName, OsArchitecture, CsTotalPhysicalMemory')">💻 Computer Info</button>
            </div>
        </aside>

        <!-- Main Workspace -->
        <main>
            <div class="tabs">
                <div class="tab active" onclick="switchTab('terminal')">Interactive Terminal</div>
                <div class="tab" onclick="switchTab('fleet')">Fleet Nodes (Zero Trust)</div>
                <div class="tab" onclick="switchTab('logs')">Audit Logs</div>
                <div class="tab" onclick="switchTab('ai-guide')">AI Integration Setup</div>
            </div>

            <!-- Terminal Tab -->
            <div id="tab-terminal" class="tab-content active">
                <div class="target-selector-bar">
                    <span style="font-weight: 500; color: var(--text-muted);">Target Computer:</span>
                    <select id="targetNodeSelect">
                        <option value="local">💻 This Computer (Local)</option>
                        <option value="__broadcast__">📡 Broadcast to All Connected Nodes</option>
                    </select>
                    <span style="color: var(--text-muted); font-size: 0.8rem; margin-left: auto;">Zero Trust Identity-Verified Execution</span>
                </div>

                <div class="terminal-output" id="terminalOut">PS Commander & Zero Trust Fleet ready. Select a target computer or broadcast across all nodes.
Server Address: http://localhost:{settings.PORT}
Swagger API Docs: http://localhost:{settings.PORT}/docs
</div>
                <div class="terminal-input-container">
                    <span class="terminal-prompt">PS &gt;</span>
                    <input type="text" id="cmdInput" class="terminal-input" placeholder="Type PowerShell command here (e.g. Get-Process or ipconfig)..." onkeydown="if(event.key==='Enter') executeCommand()">
                    <button class="run-btn" id="runBtn" onclick="executeCommand()">Execute</button>
                </div>
            </div>

            <!-- Fleet Management Tab -->
            <div id="tab-fleet" class="tab-content">
                <div class="card" style="margin-bottom: 1rem;">
                    <h2>Enroll New Computer Node (Zero Trust)</h2>
                    <div class="form-row">
                        <div class="form-group">
                            <label>Node ID (unique, e.g. office-laptop)</label>
                            <input type="text" id="newNodeId" placeholder="office-laptop">
                        </div>
                        <div class="form-group">
                            <label>Display Name</label>
                            <input type="text" id="newNodeName" placeholder="My Office Laptop">
                        </div>
                        <div class="form-group">
                            <label>Zero Trust Base URL (Tailscale / Cloudflare / LAN)</label>
                            <input type="text" id="newNodeUrl" placeholder="http://100.x.y.z:8080">
                        </div>
                        <div class="form-group">
                            <label>Node API Key</label>
                            <input type="password" id="newNodeKey" placeholder="node_api_key...">
                        </div>
                    </div>
                    <button class="run-btn" onclick="enrollNode()" style="background: #238636;">+ Enroll Computer</button>
                </div>

                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <h3>Enrolled Fleet Computers (<span id="nodeCount">1</span>)</h3>
                    <button class="node-btn" onclick="loadFleetNodes()">↻ Refresh Fleet</button>
                </div>

                <div class="fleet-grid" id="fleetGrid">
                    <!-- Populated by JS -->
                </div>
            </div>

            <!-- Audit Logs Tab -->
            <div id="tab-logs" class="tab-content">
                <div style="margin-bottom: 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.9rem; color: var(--text-muted);">Recent commands executed by AI or users:</span>
                    <button onclick="loadLogs()" class="run-btn" style="background:#21262d; border:1px solid #30363d; font-size:0.8rem; padding: 4px 10px;">↻ Reload Logs</button>
                </div>
                <div class="terminal-output" id="logsOut" style="color: #c9d1d9;">Loading logs...</div>
            </div>

            <!-- AI Guide Tab -->
            <div id="tab-ai-guide" class="tab-content">
                <div class="ai-guide">
                    <h2>Zero Trust Fleet Control for AI</h2>
                    <p>You can let any AI agent (Claude, ChatGPT, Cursor, LangChain) orchestrate commands on this machine and all enrolled remote machines securely without open router ports.</p>

                    <h3>1. Zero Trust Network Setup (Tailscale)</h3>
                    <p>To connect computers securely across different locations without opening ports:</p>
                    <ul style="margin-left: 1.5rem;">
                        <li>Install <strong>Tailscale</strong> on this computer and the remote computers.</li>
                        <li>On the remote machine, run <code>python agent_node.py --port 8080</code>.</li>
                        <li>In the <strong>Fleet Nodes</strong> tab, add the remote machine's Tailscale IP (e.g. <code>http://100.80.20.10:8080</code>).</li>
                    </ul>

                    <h3>2. Claude Desktop (MCP Multi-Node)</h3>
                    <p>Configure <code>claude_desktop_config.json</code>:</p>
                    <div class="code-box">
{{
  "mcpServers": {{
    "powershell-fleet": {{
      "command": "python",
      "args": ["{str(settings.BASE_DIR / 'mcp_server.py').replace('\\\\', '/')}"]
    }}
  }}
}}
                    </div>
                    <p>Claude can now call <code>list_nodes()</code>, <code>powershell_execute(node_id="office-pc", command="...")</code>, or <code>broadcast_command(command="...")</code>!</p>

                    <h3>3. AI Agent (Python / LangChain) Example</h3>
                    <div class="code-box">
import requests

HUB_URL = "http://localhost:{settings.PORT}"
API_KEY = "{api_key}"

# 1. Ask Hub for available machines
nodes = requests.get(f"{{HUB_URL}}/api/nodes", headers={{"Authorization": f"Bearer {{API_KEY}}" }}).json()

# 2. Run PowerShell on a specific remote machine
res = requests.post(
    f"{{HUB_URL}}/api/execute",
    headers={{"Authorization": f"Bearer {{API_KEY}}", "Content-Type": "application/json"}},
    json={{"node_id": "office-laptop", "command": "Get-Process | Select-Object -First 5"}}
)
print(res.json()["stdout"])
                    </div>
                </div>
            </div>
        </main>
    </div>

    <script>
        let currentNodes = [];

        function getAuthHeader() {{
            const key = document.getElementById('apiKeyInput').value.trim();
            return key ? {{ 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' }} : {{ 'Content-Type': 'application/json' }};
        }}

        function saveApiKey() {{
            const key = document.getElementById('apiKeyInput').value.trim();
            localStorage.setItem('rps_api_key', key);
            alert('API Key saved!');
            refreshSysInfo();
            loadFleetNodes();
        }}

        window.onload = function() {{
            const saved = localStorage.getItem('rps_api_key');
            if (saved) {{
                document.getElementById('apiKeyInput').value = saved;
            }}
            refreshSysInfo();
            loadFleetNodes();
            setInterval(refreshSysInfo, 10000);
        }};

        async function refreshSysInfo() {{
            try {{
                const res = await fetch('/api/system-info', {{ headers: getAuthHeader() }});
                if (res.ok) {{
                    const data = await res.json();
                    document.getElementById('statHost').innerText = data.hostname;
                    document.getElementById('statOs').innerText = data.os;
                    
                    document.getElementById('statCpu').innerText = data.cpu_usage_percent + '%';
                    document.getElementById('barCpu').style.width = data.cpu_usage_percent + '%';

                    document.getElementById('statRam').innerText = data.ram_used_gb + ' / ' + data.ram_total_gb + ' GB (' + data.ram_percent + '%)';
                    document.getElementById('barRam').style.width = data.ram_percent + '%';

                    document.getElementById('statDisk').innerText = data.disk_c_percent + '% (' + data.disk_c_free_gb + ' GB free)';
                    document.getElementById('barDisk').style.width = data.disk_c_percent + '%';
                }}
            }} catch(e) {{}}
        }}

        async function loadFleetNodes() {{
            try {{
                const res = await fetch('/api/nodes', {{ headers: getAuthHeader() }});
                if (res.ok) {{
                    currentNodes = await res.json();
                    document.getElementById('nodeCount').innerText = currentNodes.length;

                    // Update selector dropdown in Terminal
                    const sel = document.getElementById('targetNodeSelect');
                    const curVal = sel.value;
                    sel.innerHTML = '<option value="__broadcast__">📡 Broadcast to All Connected Nodes</option>';
                    currentNodes.forEach(n => {{
                        const opt = document.createElement('option');
                        opt.value = n.node_id;
                        opt.innerText = (n.node_id === 'local' ? '💻 ' : '🖥️ ') + n.name + ' (' + n.node_id + ')';
                        sel.appendChild(opt);
                    }});
                    if (curVal && Array.from(sel.options).some(o => o.value === curVal)) {{
                        sel.value = curVal;
                    }} else {{
                        sel.value = 'local';
                    }}

                    // Render Fleet Grid
                    const grid = document.getElementById('fleetGrid');
                    grid.innerHTML = '';
                    currentNodes.forEach(n => {{
                        const card = document.createElement('div');
                        card.className = 'node-card ' + (n.node_id === 'local' ? 'online' : 'remote');
                        card.innerHTML = `
                            <div class="node-header">
                                <div class="node-title">${{n.name}}</div>
                                <span class="node-badge">${{n.node_id}}</span>
                            </div>
                            <div class="node-detail"><strong>URL:</strong> ${{n.base_url}}</div>
                            <div class="node-detail"><strong>Tags:</strong> ${{n.tags.join(', ') || 'none'}}</div>
                            <div class="node-actions">
                                <button class="node-btn" onclick="pingNode('${{n.node_id}}')">⚡ Check Status</button>
                                ${{n.node_id !== 'local' ? `<button class="node-btn danger" onclick="removeNode('${{n.node_id}}')">✕ Remove</button>` : ''}}
                            </div>
                        `;
                        grid.appendChild(card);
                    }});
                }}
            }} catch(e) {{
                console.error('Fleet load error:', e);
            }}
        }}

        async function enrollNode() {{
            const id = document.getElementById('newNodeId').value.trim();
            const name = document.getElementById('newNodeName').value.trim();
            const url = document.getElementById('newNodeUrl').value.trim();
            const key = document.getElementById('newNodeKey').value.trim();

            if (!id || !name || !url || !key) {{
                alert('Please fill out all fields to enroll a node.');
                return;
            }}

            try {{
                const res = await fetch('/api/nodes/register', {{
                    method: 'POST',
                    headers: getAuthHeader(),
                    body: JSON.stringify({{ node_id: id, name: name, base_url: url, api_key: key, tags: ['remote'] }})
                }});
                if (res.ok) {{
                    alert(`Node '${{name}}' successfully enrolled!`);
                    document.getElementById('newNodeId').value = '';
                    document.getElementById('newNodeName').value = '';
                    document.getElementById('newNodeUrl').value = '';
                    document.getElementById('newNodeKey').value = '';
                    loadFleetNodes();
                }} else {{
                    alert('Error enrolling node: ' + (await res.text()));
                }}
            }} catch(e) {{
                alert('Connection error: ' + e.message);
            }}
        }}

        async function removeNode(nodeId) {{
            if (!confirm(`Are you sure you want to remove node '${{nodeId}}'?`)) return;
            try {{
                const res = await fetch(`/api/nodes/${{nodeId}}`, {{ method: 'DELETE', headers: getAuthHeader() }});
                if (res.ok) {{
                    loadFleetNodes();
                }} else {{
                    alert('Failed to remove: ' + (await res.text()));
                }}
            }} catch(e) {{ alert('Error: ' + e.message); }}
        }}

        async function pingNode(nodeId) {{
            try {{
                const res = await fetch(`/api/system-info?node_id=${{nodeId}}`, {{ headers: getAuthHeader() }});
                const data = await res.json();
                if (data.error) {{
                    alert(`Node '${{nodeId}}' offline/error: ${{data.error}}`);
                }} else {{
                    alert(`Node '${{nodeId}}' is ONLINE!\\nOS: ${{data.os || data.hostname}}\\nCPU: ${{data.cpu_usage_percent}}%\\nFree Disk: ${{data.disk_free_gb || data.disk_c_free_gb}} GB`);
                }}
            }} catch(e) {{
                alert(`Ping failed: ${{e.message}}`);
            }}
        }}

        async function executeCommand() {{
            const input = document.getElementById('cmdInput');
            const command = input.value.trim();
            if (!command) return;

            const target = document.getElementById('targetNodeSelect').value;
            const term = document.getElementById('terminalOut');
            term.textContent += `\\n\\n[${{new Date().toLocaleTimeString()}}] [Target: ${{target}}] PS> ${{command}}\\n[Executing...]`;
            term.scrollTop = term.scrollHeight;
            input.value = '';

            try {{
                let res;
                if (target === '__broadcast__') {{
                    res = await fetch('/api/nodes/broadcast', {{
                        method: 'POST',
                        headers: getAuthHeader(),
                        body: JSON.stringify({{ command: command }})
                    }});
                }} else {{
                    res = await fetch('/api/execute', {{
                        method: 'POST',
                        headers: getAuthHeader(),
                        body: JSON.stringify({{ command: command, node_id: target }})
                    }});
                }}

                if (!res.ok) {{
                    const err = await res.text();
                    term.textContent += `\\n[HTTP Error ${{res.status}}]: ${{err}}`;
                }} else {{
                    const data = await res.json();
                    if (target === '__broadcast__') {{
                        term.textContent += `\\n[Broadcast Result across ${{data.total_nodes}} nodes]:`;
                        data.results.forEach(r => {{
                            term.textContent += `\\n\\n=== Node: ${{r.node_name || r.node_id}} (Exit: ${{r.exit_code}}) ===`;
                            if (r.stdout) term.textContent += `\\n${{r.stdout}}`;
                            if (r.stderr) term.textContent += `\\n[STDERR] ${{r.stderr}}`;
                        }});
                    }} else {{
                        if (data.stdout) term.textContent += `\\n${{data.stdout}}`;
                        if (data.stderr) term.textContent += `\\n[STDERR]\\n${{data.stderr}}`;
                        term.textContent += `\\n[Node: ${{data.node_name || data.node_id}} | Exit Code: ${{data.exit_code}} | Duration: ${{data.duration_ms}}ms]`;
                    }}
                }}
            }} catch(e) {{
                term.textContent += `\\n[Network/Client Error]: ${{e.message}}`;
            }}
            term.scrollTop = term.scrollHeight;
        }}

        function runQuick(cmd) {{
            document.getElementById('cmdInput').value = cmd;
            switchTab('terminal');
            executeCommand();
        }}

        function switchTab(tabId) {{
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            const selectedTab = Array.from(document.querySelectorAll('.tab')).find(t => t.innerText.toLowerCase().includes(tabId));
            if (selectedTab) selectedTab.classList.add('active');
            
            const targetContent = document.getElementById('tab-' + tabId);
            if (targetContent) targetContent.classList.add('active');

            if (tabId === 'logs') loadLogs();
            if (tabId === 'fleet') loadFleetNodes();
        }}

        async function loadLogs() {{
            const box = document.getElementById('logsOut');
            box.textContent = 'Loading audit logs...';
            try {{
                const res = await fetch('/api/logs?limit=50', {{ headers: getAuthHeader() }});
                if (res.ok) {{
                    const logs = await res.json();
                    if (logs.length === 0) {{
                        box.textContent = 'No audit logs recorded yet.';
                        return;
                    }}
                    box.textContent = logs.map(l => 
                        `[${{l.timestamp}}] (${{l.duration_ms}}ms | Exit: ${{l.exit_code}})\\nCWD: ${{l.cwd}}\\nCMD: ${{l.command}}\\nOUT: ${{l.stdout_preview || '-'}}\\n------------------------------------------------`
                    ).join('\\n\\n');
                }} else {{
                    box.textContent = 'Failed to load logs. Status: ' + res.status;
                }}
            }} catch(e) {{
                box.textContent = 'Error loading logs: ' + e.message;
            }}
        }}
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  [*] ZERO TRUST REMOTE POWERSHELL COMMANDER & FLEET HUB")
    print(f"  Web Dashboard: http://{settings.HOST}:{settings.PORT}")
    print(f"  Swagger Docs:  http://{settings.HOST}:{settings.PORT}/docs")
    print(f"  Master Key:    {ensure_api_key()}")
    print("=" * 60)
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
