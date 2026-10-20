"""python -m backend  ->  the analysis backend on http://127.0.0.1:8765 (this machine only)."""
import os

import uvicorn

uvicorn.run("backend.app:app", host="127.0.0.1", port=int(os.environ.get("PHANTOM_PORT", "8765")))
