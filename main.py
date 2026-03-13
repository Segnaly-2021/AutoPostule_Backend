import uvicorn
from auto_apply_app.infrastructures.api.app import create_fastapi_app


app = create_fastapi_app()

if __name__ == "__main__":
    
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=False,
    )