"""structlog configuration producing structured JSON logs.

Every log line carries enough context (event, symbol, order_id, correlation_id)
to reconstruct the full Signal -> Submission -> Ack -> Fill lifecycle of an order
purely from log output.
"""
from __future__ import annotations

import logging
import sys
import time

import structlog


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer(serializer=_orjson_dumps)
        if json_output
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    root_handler = logging.getLogger().handlers[0]
    root_handler.setFormatter(formatter)


def _orjson_dumps(obj: object, **_: object) -> str:
    import orjson

    return orjson.dumps(obj, default=str).decode("utf-8")


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def monotonic_ns() -> int:
    return time.monotonic_ns()
