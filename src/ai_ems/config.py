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

# Trusted operator configuration; no HTTP request can select a local map path.
KOREA_MAP_FILE = _path_from_env("AI_EMS_KOREA_MAP_FILE", "data/grids/kr-osm-transmission-map.json")

# Mongolia PyPowSyBl model supplied (read-only) by the sibling mongolia-grid-poc repository.
# Server-side settings only; HTTP requests can never choose these paths.
MONGOLIA_XIIDM_FILE = _path_from_env(
    "AI_EMS_MN_XIIDM_FILE",
    "../mongolia-grid-poc/pypsa-earth/networks/MN_35k_poc/powsybl/mn35k_synthetic_powsybl.xiidm",
)

MONGOLIA_POWSYBL_REPORTS_DIR = _path_from_env(
    "AI_EMS_MN_POWSYBL_REPORTS_DIR",
    "../mongolia-grid-poc/reports/pypowsybl_conversion",
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


def get_llm_client_kwargs(base_url: str, *, environ=None) -> dict:
    """Return optional authentication headers for both Ollama HTTP clients."""
    settings = os.environ if environ is None else environ
    api_key = settings.get("AI_EMS_LLM_API_KEY", "").strip()
    client_id = settings.get("AI_EMS_CF_ACCESS_CLIENT_ID", "").strip()
    client_secret = settings.get("AI_EMS_CF_ACCESS_CLIENT_SECRET", "").strip()
    if api_key and (client_id or client_secret):
        raise ValueError(
            "AI_EMS_LLM_API_KEY and Cloudflare Access credentials cannot be set together."
        )
    if not api_key and not client_id and not client_secret:
        return {}
    if not api_key and (not client_id or not client_secret):
        raise ValueError(
            "Set both AI_EMS_CF_ACCESS_CLIENT_ID and "
            "AI_EMS_CF_ACCESS_CLIENT_SECRET, or leave both unset."
        )
    if any("\r" in value or "\n" in value for value in (api_key, client_id, client_secret)):
        raise ValueError("LLM authentication values must not contain line breaks.")

    url = urlsplit(base_url)
    secure_remote = (
        url.scheme.lower() == "https"
        and bool(url.hostname)
        and url.username is None
        and url.password is None
    )
    local_proxy = base_url in (
        "http://127.0.0.1:18080",
        "http://127.0.0.1:18080/",
    )
    if not secure_remote and not (api_key and local_proxy):
        raise ValueError(
            "LLM authentication requires HTTPS; Bearer authentication also allows "
            "the local test proxy http://127.0.0.1:18080."
        )

    headers = (
        {"Authorization": f"Bearer {api_key}"}
        if api_key
        else {
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        }
    )
    return {"headers": headers}
