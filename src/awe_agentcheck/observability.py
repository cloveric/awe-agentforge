from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar

_task_id_var: ContextVar[str | None] = ContextVar('task_id', default=None)
_round_var: ContextVar[int | None] = ContextVar('round_no', default=None)


def set_task_context(task_id: str | None = None, round_no: int | None = None) -> None:
    """Set correlation context for structured log output."""
    _task_id_var.set(task_id)
    _round_var.set(round_no)


def get_task_id() -> str | None:
    return _task_id_var.get(None)


def get_round_no() -> int | None:
    return _round_var.get(None)


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line with correlation fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            'ts': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
        }
        task_id = getattr(record, 'task_id', None) or _task_id_var.get(None)
        if task_id:
            payload['task_id'] = task_id
        round_no = getattr(record, 'round_no', None) or _round_var.get(None)
        if round_no is not None:
            payload['round'] = round_no
        if record.exc_info and record.exc_info[1] is not None:
            payload['exc'] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger. Safe to call before configure_observability."""
    return logging.getLogger(name)


def configure_observability(*, service_name: str, otlp_endpoint: str | None) -> None:
    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JsonFormatter())
        root = logging.getLogger('awe_agentcheck')
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        _configured = True

    if not otlp_endpoint:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:
        logging.getLogger('awe_agentcheck.observability').warning(
            'OpenTelemetry import failed; tracing disabled', exc_info=True,
        )
        return

    provider = TracerProvider(resource=Resource.create({'service.name': service_name}))
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
