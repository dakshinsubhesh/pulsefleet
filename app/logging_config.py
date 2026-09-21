"""
PulseFleet — Structured logging (Day 11: Reliability)

Every log line is a single JSON object, so logs are grep/query-able in
any log aggregator without regex parsing. Two loggers matter in
practice:
- "pulsefleet.request" — one line per HTTP request (method, path,
  status, duration, request_id)
- "pulsefleet.error"   — one line per unexpected (5xx) failure, with
  the full exception type/message/traceback attached

Expected 4xx errors (validation, not-found, conflict, auth) are NOT
logged as errors — they're normal client-caused outcomes, and logging
every 404 as an "error" would bury the unexpected failures under noise.
"""
import json
import logging
import sys
import traceback
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Any extra= fields passed to the logging call are merged in flat,
        # so e.g. logger.info("request", extra={"request_id": "..."}) shows
        # up as a top-level "request_id" key, not buried in a sub-object.
        for key, value in record.__dict__.items():
            if key in (
                "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "created", "msecs", "relativeCreated", "thread", "threadName",
                "processName", "process", "taskName", "message",
            ):
                continue
            payload[key] = value

        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            payload["exception"] = {
                "type": exc_type.__name__ if exc_type else None,
                "message": str(exc_value),
                "traceback": traceback.format_exception(exc_type, exc_value, exc_tb),
            }

        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # Quiet down noisy third-party loggers to INFO-and-above only.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # we log requests ourselves


request_logger = logging.getLogger("pulsefleet.request")
error_logger = logging.getLogger("pulsefleet.error")
