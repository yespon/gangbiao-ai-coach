from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.router import legacy_api_router
from app.api.v1.router import api_v1_router
from app.core.config import get_cors_allow_origin_regex, get_cors_allow_origins
from app.core.logger import attach_request_logging_middleware, get_component_logger, setup_logging
from app.services.context_service import MATERIALS_CONTEXT_CACHE
from app.services.session_service import SESSIONS

setup_logging()


app = FastAPI(title="Gangbiao Chatbot", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_allow_origins(),
    allow_origin_regex=get_cors_allow_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LOGGER = get_component_logger(component="chatbot")

attach_request_logging_middleware(app, LOGGER)
app.include_router(legacy_api_router, prefix="/api")
app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/")
async def index() -> dict[str, str]:
    return {
        "service": "gangbiao-chatbot-api",
        "status": "ok",
        "version": "0.1.0",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=2024, reload=True)
