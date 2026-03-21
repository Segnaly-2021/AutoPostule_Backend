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
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=False,
    )