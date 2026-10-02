import json
import time
import platform
from pathlib import Path
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
import httpx
from config import settings

NODES_FILE = settings.BASE_DIR / "nodes.json"

def get_current_remote_id() -> str:
    """Reads the active remote ID from .remote_id if present."""
    rid_file = settings.BASE_DIR / ".remote_id"
    if rid_file.exists():
        try:
            data = json.loads(rid_file.read_text(encoding="utf-8"))
            return str(data.get("remote_id", "")).strip()
        except Exception:
            pass
    return ""

class NodeInfo(BaseModel):
    node_id: str
    name: str
    base_url: str  # e.g. "http://100.x.y.z:8080" (Tailscale Zero Trust IP) or "https://pc2.your-domain.com"
    api_key: str
    tags: List[str] = Field(default_factory=list)
    last_seen: Optional[float] = None
    is_active: bool = True
    os_info: Optional[str] = "Windows"

class NodeRegistry:
    def __init__(self, filepath: Path = NODES_FILE):
        self.filepath = filepath
        self.nodes: Dict[str, NodeInfo] = {}
        self._load()

    def _load(self):
        remote_id = get_current_remote_id()
        primary_id = f"pc-{remote_id}" if remote_id else "local"
        display_name = f"This Computer ({primary_id})"
        tags = ["local", "host"]
        if remote_id:
            tags.extend([remote_id, f"pc-{remote_id}"])

        # Always register local node
        local_node = NodeInfo(
            node_id="local",
            name=display_name,
            base_url=f"http://127.0.0.1:{settings.PORT}",
            api_key=settings.REMOTE_SHELL_API_KEY,
            tags=tags,
            last_seen=time.time(),
            is_active=True
        )
        self.nodes["local"] = local_node
        self.nodes["host"] = local_node
        self.nodes["localhost"] = local_node
        self.nodes[platform.node().lower()] = local_node
        self.nodes[f"pc-{platform.node().lower()}"] = local_node

        # Map Remote ID aliases so Claude can address it by pc-XXX-XXX directly
        if remote_id:
            self.nodes[remote_id] = local_node
            self.nodes[f"pc-{remote_id}"] = local_node
            clean_rid = remote_id.replace("-", "")
            self.nodes[clean_rid] = local_node
            self.nodes[f"pc{clean_rid}"] = local_node

        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
                for item in data:
                    node = NodeInfo(**item)
                    self.nodes[node.node_id] = node
            except Exception as e:
                print(f"[NODE REGISTRY] Error loading {self.filepath}: {e}")

    def _save(self):
        try:
            # Save non-local nodes to JSON
            exportable = [
                node.model_dump()
                for nid, node in self.nodes.items()
                if nid not in ["local", "host", "localhost"] and not nid.startswith("pc-")
            ]
            self.filepath.write_text(json.dumps(exportable, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[NODE REGISTRY] Error saving {self.filepath}: {e}")

    def register_node(self, node: NodeInfo) -> NodeInfo:
        self.nodes[node.node_id] = node
        self._save()
        return node

    def remove_node(self, node_id: str) -> bool:
        if node_id in self.nodes and node_id != "local":
            del self.nodes[node_id]
            self._save()
            return True
        return False

    def list_nodes(self) -> List[NodeInfo]:
        remote_id = get_current_remote_id()
        primary_id = f"pc-{remote_id}" if remote_id else "local"
        local = self.nodes.get("local")

        results = []
        seen = set()

        if local:
            host_repr = NodeInfo(
                node_id=primary_id,
                name=f"This Remote Computer ({primary_id})",
                base_url=local.base_url,
                api_key=local.api_key,
                tags=local.tags,
                last_seen=time.time(),
                is_active=True
            )
            results.append(host_repr)
            seen.update(["local", "host", "localhost", primary_id])
            if remote_id:
                seen.update([remote_id, f"pc-{remote_id}", remote_id.replace("-", "")])

        for nid, n in self.nodes.items():
            if nid not in seen and n.node_id not in seen:
                results.append(n)
                seen.add(nid)
                seen.add(n.node_id)
        return results

    def get_node(self, node_id: Optional[str]) -> NodeInfo:
        """Finds the node or defaults to the local computer so commands never fail with 'node not found'."""
        local = self.nodes.get("local")
        if not node_id or not str(node_id).strip():
            return local

        nid = str(node_id).strip().lower()

        # Direct match
        for k, v in self.nodes.items():
            if k.lower() == nid:
                return v

        # Normalized match (removing pc- prefix or dashes)
        clean = nid.replace("pc-", "").replace("pc", "").replace("-", "")
        for k, v in self.nodes.items():
            k_clean = k.lower().replace("pc-", "").replace("pc", "").replace("-", "")
            if k_clean == clean:
                return v

        remote_id = get_current_remote_id()
        if remote_id:
            rid_clean = remote_id.lower().replace("-", "")
            if clean == rid_clean:
                return local

        # Fallback to local host so Claude can always execute
        return local

    async def execute_on_node(self, node_id: str, command: str, cwd: Optional[str] = None, timeout: Optional[int] = 60) -> Dict[str, Any]:
        """Dispatches command execution to target node, directly executing locally if targeting this machine."""
        node = self.get_node(node_id)
        local = self.nodes.get("local")

        # If it's local or points to localhost, execute directly via PowerShellExecutor
        if not node or node == local or node.node_id in ["local", "host"] or "127.0.0.1" in node.base_url or "localhost" in node.base_url:
            from executor import PowerShellExecutor
            res = PowerShellExecutor.execute(command=command, cwd=cwd, timeout=timeout)
            res["node_id"] = node_id or "local"
            res["node_name"] = node.name if node else "Local Host"
            return res

        # Otherwise, relay via HTTP/HTTPS over Zero Trust Network (Tailscale/Cloudflare)
        url = f"{node.base_url.rstrip('/')}/api/execute"
        headers = {
            "Authorization": f"Bearer {node.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "command": command,
            "cwd": cwd,
            "timeout": timeout
        }

        start_t = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout + 5) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    data["node_id"] = node_id
                    data["node_name"] = node.name
                    node.last_seen = time.time()
                    return data
                else:
                    return {
                        "success": False,
                        "node_id": node_id,
                        "node_name": node.name,
                        "exit_code": resp.status_code,
                        "stdout": "",
                        "stderr": f"[HTTP {resp.status_code}] Remote node response: {resp.text}",
                        "duration_ms": round((time.time() - start_t) * 1000, 2),
                        "timestamp": str(time.time())
                    }
        except httpx.TimeoutException:
            return {
                "success": False,
                "node_id": node_id,
                "node_name": node.name,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"[TIMEOUT] Remote node '{node.name}' timed out after {timeout} seconds.",
                "duration_ms": round((time.time() - start_t) * 1000, 2),
                "timestamp": str(time.time())
            }
        except Exception as e:
            return {
                "success": False,
                "node_id": node_id,
                "node_name": node.name,
                "exit_code": -2,
                "stdout": "",
                "stderr": f"[CONNECTION FAILED] Unable to reach node at {node.base_url}: {str(e)}",
                "duration_ms": round((time.time() - start_t) * 1000, 2),
                "timestamp": str(time.time())
            }

    async def get_node_sysinfo(self, node_id: str) -> Dict[str, Any]:
        """Fetches system diagnostics from local computer or remote node."""
        node = self.get_node(node_id)
        local = self.nodes.get("local")

        if not node or node == local or node.node_id in ["local", "host"] or "127.0.0.1" in node.base_url or "localhost" in node.base_url:
            from executor import PowerShellExecutor
            data = PowerShellExecutor.get_system_info()
            data["node_id"] = node_id or "local"
            data["node_name"] = node.name if node else "Local Host"
            return data

        url = f"{node.base_url.rstrip('/')}/api/system-info"
        headers = {"Authorization": f"Bearer {node.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    data["node_id"] = node_id
                    data["node_name"] = node.name
                    node.last_seen = time.time()
                    return data
                return {"error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            return {"error": f"Failed to connect to {node.base_url}: {str(e)}"}

node_registry = NodeRegistry()
