"""Test subprocess bootstrap: prove masked files cannot be read, then run real Uvicorn."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

if __name__ == "__main__":
    root = Path.cwd()
    sys.path[:0] = [str(root/"src"), str(root)]
    evidence = []
    for name in json.loads((root/"isolation.json").read_text()):
        try:
            Path(name).read_bytes()
        except (FileNotFoundError, PermissionError) as exc:
            evidence.append({"path": name, "read_blocked": True, "error": type(exc).__name__})
        else:
            raise RuntimeError(f"Isolation failed: external file readable: {name}")
    assert importlib.util.find_spec("pypsa") is None, "Build-only PyPSA leaked into runtime"
    assert shutil.which("pixi") is None, "Pixi must not be required by the isolated process"
    print(json.dumps({"isolation_proof": evidence, "pypsa_available": False, "pixi_available": False}), flush=True)
    import uvicorn
    uvicorn.run(sys.argv[1], host="127.0.0.1", port=int(sys.argv[2]), log_level="info")
