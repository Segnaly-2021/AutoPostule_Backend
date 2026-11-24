# ==============================================================================
# FILE: main.py
#
# WHAT IT DOES:
#   Entry point of the HTTP API. Importing this module builds the FastAPI app and
#   exposes it as `app`, which is what `uvicorn main:app` serves.
#   Production (Dockerfile, Dockerfile.worker) runs uvicorn directly on $PORT.
#   The `__main__` block below is only for local runs with `python main.py`.
#
# USED BY:
#   - Dockerfile: starts the API with `uvicorn main:app`
#   - Dockerfile.worker: same command, for the free-search Cloud Run service
#
# EXTERNAL DEPENDENCIES:
#   - uvicorn: ASGI server that runs the FastAPI app
#   - Google Cloud Run / Cloud Logging: where the boot errors printed here end up
# ==============================================================================

import uvicorn
import sys
import traceback


print("🚀 Starting boot sequence...", file=sys.stderr)

try:
    from auto_apply_app.infrastructures.api.app import create_fastapi_app
    app = create_fastapi_app()
    print("✅ FastAPI app created successfully!", file=sys.stderr)
    
except Exception as e:
    # This will blast the exact error into GCP logs on one single line
    print(f"🚨 FATAL BOOT ERROR: {repr(e)}", file=sys.stderr)
    print("🚨 TRACEBACK:", file=sys.stderr)
    traceback.print_exc()
    raise e

if __name__ == "__main__":
    # NOTE: possible issue: the string "main:app" makes uvicorn import this file again
    # as module `main`, so `python main.py` builds the FastAPI app twice.
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=False,
    )