from fastapi import FastAPI

app = FastAPI(
    title="Trading Terminal API",
    version="0.1.0",
    description="Backend API for AI-assisted trading terminal",
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "backend",
        "version": "0.1.0",
    }
