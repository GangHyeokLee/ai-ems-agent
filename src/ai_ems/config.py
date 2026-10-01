import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(
    PROJECT_ROOT / ".env",
    override=False,
)


def _path_from_env(
    name: str,
    default: str,
) -> Path:
    value = os.getenv(name, default)
    path = Path(value)

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


MODEL_NAME = os.getenv(
    "AI_EMS_MODEL",
    "qwen2:7b",
)

LLM_BASE_URL = os.getenv(
    "AI_EMS_LLM_BASE_URL",
    "http://host.docker.internal:11434",
)

CASE_FILE = _path_from_env(
    "AI_EMS_CASE_FILE",
    "data/KPG193_ver2_0_powsybl_full.mat",
)

BUS_LOCATION_FILE = _path_from_env(
    "AI_EMS_BUS_LOCATION_FILE",
    "data/bus_location.csv",
)

WEB_HOST = os.getenv(
    "AI_EMS_WEB_HOST",
    "127.0.0.1",
)

WEB_PORT = int(
    os.getenv(
        "AI_EMS_WEB_PORT",
        "8000",
    )
)

PHYSICS_HOST = os.getenv(
    "AI_EMS_PHYSICS_HOST",
    "127.0.0.1",
)

PHYSICS_PORT = int(
    os.getenv(
        "AI_EMS_PHYSICS_PORT",
        "8001",
    )
)

LOG_LEVEL = os.getenv(
    "AI_EMS_LOG_LEVEL",
    "info",
)


def get_llm_client_kwargs(base_url: str) -> dict:
    """Return optional Access headers for both Ollama HTTP clients."""
    client_id = os.getenv("AI_EMS_CF_ACCESS_CLIENT_ID", "").strip()
    client_secret = os.getenv("AI_EMS_CF_ACCESS_CLIENT_SECRET", "").strip()
    if not client_id and not client_secret:
        return {}
    if not client_id or not client_secret:
        raise ValueError(
            "Set both AI_EMS_CF_ACCESS_CLIENT_ID and "
            "AI_EMS_CF_ACCESS_CLIENT_SECRET, or leave both unset."
        )
    if urlsplit(base_url).scheme.lower() != "https":
        raise ValueError("Cloudflare Access credentials require an HTTPS LLM base URL.")
    return {
        "headers": {
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        },
    }
