import subprocess
import time
import json
import base64
import psutil
import platform
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from config import settings

class PowerShellExecutor:
    @staticmethod
    def execute(command: str, cwd: Optional[str] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Executes a PowerShell command synchronously with timeout, output capture,
        UTF-8 encoding support, and audit logging.
        """
        effective_timeout = min(timeout or settings.DEFAULT_TIMEOUT, settings.MAX_TIMEOUT)
        effective_cwd = cwd if cwd and os.path.isdir(cwd) else str(settings.DEFAULT_WORKDIR)
        
        # Prepare wrapper to force UTF-8 output encoding and suppress progress stream CLIXML noise
        ps_wrapper = (
            "$ProgressPreference = 'SilentlyContinue';\n"
            "$OutputEncoding = [System.Text.Encoding]::UTF8;\n"
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;\n"
            f"{command}\n"
        )
        # Encode command in UTF-16LE and Base64 for PowerShell -EncodedCommand
        encoded_cmd = base64.b64encode(ps_wrapper.encode("utf-16le")).decode("ascii")

        start_time = time.time()
        start_iso = datetime.now(timezone.utc).isoformat()
        
        try:
            # We call powershell.exe with -EncodedCommand for robust parsing
            process = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy", "Bypass",
                    "-EncodedCommand", encoded_cmd
                ],
                cwd=effective_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            
            stdout, stderr = process.communicate(timeout=effective_timeout)
            exit_code = process.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            exit_code = -1
            timed_out = True
            stderr = f"{stderr}\n[TIMEOUT] Command timed out after {effective_timeout} seconds."
        except Exception as e:
            stdout = ""
            stderr = f"[ERROR] Failed to execute command: {str(e)}"
            exit_code = -2
            timed_out = False

        # Clean any remaining PowerShell CLIXML progress noise from stderr
        if stderr and "#< CLIXML" in stderr and "<Obj S=\"progress\"" in stderr and not "<Obj S=\"Error\"" in stderr:
            stderr = ""

        duration_ms = round((time.time() - start_time) * 1000, 2)

        # Truncate output if it exceeds max allowed size
        truncated = False
        if len(stdout) > settings.MAX_OUTPUT_CHARS:
            stdout = stdout[:settings.MAX_OUTPUT_CHARS] + f"\n... [Output truncated: exceeded {settings.MAX_OUTPUT_CHARS} characters]"
            truncated = True

        result = {
            "success": (exit_code == 0 and not timed_out),
            "command": command,
            "cwd": effective_cwd,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "truncated": truncated,
            "timestamp": start_iso
        }

        # Write to audit log
        PowerShellExecutor._log_audit(result)

        return result

    @staticmethod
    def _log_audit(entry: Dict[str, Any]):
        try:
            log_line = {
                "timestamp": entry["timestamp"],
                "command": entry["command"][:500],  # record preview of command
                "cwd": entry["cwd"],
                "exit_code": entry["exit_code"],
                "duration_ms": entry["duration_ms"],
                "success": entry["success"],
                "stdout_preview": entry["stdout"][:200] if entry.get("stdout") else "",
                "stderr_preview": entry["stderr"][:200] if entry.get("stderr") else ""
            }
            with open(settings.AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_line, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[AUDIT LOG ERROR] {e}")

    @staticmethod
    def get_system_info() -> Dict[str, Any]:
        """Returns hardware, OS, and resource usage info for AI context."""
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")

        # Network IPs
        ip_list = []
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family.name == "AF_INET" and not addr.address.startswith("127."):
                    ip_list.append({"interface": iface, "ip": addr.address})

        return {
            "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "hostname": platform.node(),
            "cpu_count": psutil.cpu_count(logical=True),
            "cpu_usage_percent": cpu_percent,
            "ram_total_gb": round(mem.total / (1024 ** 3), 2),
            "ram_used_gb": round(mem.used / (1024 ** 3), 2),
            "ram_percent": mem.percent,
            "disk_c_total_gb": round(disk.total / (1024 ** 3), 2),
            "disk_c_free_gb": round(disk.free / (1024 ** 3), 2),
            "disk_c_percent": disk.percent,
            "active_ips": ip_list,
            "python_version": platform.python_version(),
            "current_dir": os.getcwd()
        }

    @staticmethod
    def get_recent_audit_logs(limit: int = 50):
        if not settings.AUDIT_LOG_FILE.exists():
            return []
        try:
            with open(settings.AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            logs = [json.loads(line) for line in lines[-limit:] if line.strip()]
            logs.reverse() # newest first
            return logs
        except Exception as e:
            return [{"error": f"Failed to read logs: {e}"}]
