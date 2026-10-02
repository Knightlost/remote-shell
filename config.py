import os
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )

    # Server host and port
    HOST: str = "0.0.0.0"  # Listen on all interfaces so other computers on LAN/Tailscale can connect
    PORT: int = 8080

    # Authentication Token
    REMOTE_SHELL_API_KEY: str = ""

    # Execution limits
    DEFAULT_TIMEOUT: int = 60
    MAX_TIMEOUT: int = 300
    MAX_OUTPUT_CHARS: int = 200000

    # Paths
    BASE_DIR: Path = Path(__file__).resolve().parent
    AUDIT_LOG_FILE: Path = BASE_DIR / "audit_log.jsonl"
    DEFAULT_WORKDIR: Path = BASE_DIR

settings = Settings()

def ensure_api_key() -> str:
    env_file = settings.BASE_DIR / ".env"
    if not settings.REMOTE_SHELL_API_KEY:
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("REMOTE_SHELL_API_KEY="):
                    settings.REMOTE_SHELL_API_KEY = line.split("=", 1)[1].strip()
                    break
        
        if not settings.REMOTE_SHELL_API_KEY:
            generated_key = f"rps_{secrets.token_urlsafe(32)}"
            settings.REMOTE_SHELL_API_KEY = generated_key
            with open(env_file, "a", encoding="utf-8") as f:
                f.write(f"\nREMOTE_SHELL_API_KEY={generated_key}\n")
            print(f"[SECURITY] Generated new API Key: {generated_key}")
            print(f"[SECURITY] Saved to {env_file}")
            
    return settings.REMOTE_SHELL_API_KEY

ensure_api_key()
