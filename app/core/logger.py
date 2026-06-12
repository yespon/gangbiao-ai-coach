import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from loguru import logger

from app.core.config import BASE_DIR, settings


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging() -> None:
    log_level = settings.log_level.upper()
    log_file = settings.log_file.strip() or "app.log"
    log_rotation = settings.log_rotation.strip() or "1 day"
    log_retention = settings.log_retention.strip() or "14 days"
    log_json = settings.log_json
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        sink=sys.stderr,
        level=log_level,
        colorize=not log_json,
        serialize=log_json,
        backtrace=True,
        diagnose=False,
    )
    logger.add(
        log_dir / log_file,
        level=log_level,
        rotation=log_rotation,
        retention=log_retention,
        encoding="utf-8",
        enqueue=True,
        serialize=log_json,
        backtrace=True,
        diagnose=False,
    )

    intercept_handler = InterceptHandler()
    logging.basicConfig(handlers=[intercept_handler], level=0, force=True)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging_logger = logging.getLogger(logger_name)
        logging_logger.handlers = [intercept_handler]
        logging_logger.propagate = False


def get_component_logger(component: str):
    return logger.bind(component=component)


def attach_request_logging_middleware(app: FastAPI, app_logger) -> None:
    @app.middleware("http")
    async def log_request(request: Request, call_next: Callable[[Request], Awaitable]):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        req_logger = app_logger.bind(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        started = time.perf_counter()
        req_logger.info("request_started")

        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            req_logger.bind(elapsed_ms=round(elapsed_ms, 2)).exception("request_failed")
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        req_logger.bind(status_code=response.status_code, elapsed_ms=round(elapsed_ms, 2)).info("request_finished")
        return response
