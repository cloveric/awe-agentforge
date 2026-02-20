from __future__ import annotations

import json
import logging

from awe_agentcheck.observability import (
    _JsonFormatter,
    configure_observability,
    get_logger,
    get_round_no,
    get_task_id,
    set_task_context,
)


def test_configure_observability_no_endpoint_is_noop():
    configure_observability(service_name='awe-agentcheck', otlp_endpoint=None)


def test_configure_observability_is_idempotent_for_json_handler(monkeypatch):
    import awe_agentcheck.observability as observability

    root = logging.getLogger('awe_agentcheck')
    original_handlers = list(root.handlers)
    original_level = root.level

    try:
        for handler in list(root.handlers):
            if isinstance(handler, logging.StreamHandler) and isinstance(
                getattr(handler, 'formatter', None), _JsonFormatter
            ):
                root.removeHandler(handler)

        monkeypatch.setattr(observability, '_configured', False)
        monkeypatch.setattr(observability, '_configured_otlp_endpoint', None)

        configure_observability(service_name='awe-agentcheck', otlp_endpoint=None)
        configure_observability(service_name='awe-agentcheck', otlp_endpoint=None)

        json_handlers = [
            handler
            for handler in root.handlers
            if isinstance(handler, logging.StreamHandler)
            and isinstance(getattr(handler, 'formatter', None), _JsonFormatter)
        ]
        assert len(json_handlers) == 1
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)


def test_set_and_get_task_context():
    set_task_context(task_id='abc-123', round_no=2)
    assert get_task_id() == 'abc-123'
    assert get_round_no() == 2
    set_task_context(task_id=None, round_no=None)
    assert get_task_id() is None
    assert get_round_no() is None


def test_json_formatter_includes_correlation_fields():
    fmt = _JsonFormatter()
    set_task_context(task_id='tid-1', round_no=3)
    try:
        logger = get_logger('awe_agentcheck.test_fmt')
        record = logger.makeRecord(
            'awe_agentcheck.test_fmt', logging.INFO, 'test.py', 1,
            'hello %s', ('world',), None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed['msg'] == 'hello world'
        assert parsed['task_id'] == 'tid-1'
        assert parsed['round'] == 3
        assert parsed['level'] == 'INFO'
    finally:
        set_task_context(task_id=None, round_no=None)


def test_json_formatter_omits_missing_correlation():
    fmt = _JsonFormatter()
    set_task_context(task_id=None, round_no=None)
    logger = get_logger('awe_agentcheck.test_fmt2')
    record = logger.makeRecord(
        'awe_agentcheck.test_fmt2', logging.WARNING, 'test.py', 1,
        'no context', (), None,
    )
    output = fmt.format(record)
    parsed = json.loads(output)
    assert 'task_id' not in parsed
    assert 'round' not in parsed
    assert parsed['level'] == 'WARNING'


def test_json_formatter_includes_exception():
    fmt = _JsonFormatter()
    set_task_context(task_id=None, round_no=None)
    logger = get_logger('awe_agentcheck.test_exc')
    try:
        raise ValueError('boom')
    except ValueError:
        import sys
        exc_info = sys.exc_info()
    record = logger.makeRecord(
        'awe_agentcheck.test_exc', logging.ERROR, 'test.py', 1,
        'failed', (), exc_info,
    )
    output = fmt.format(record)
    parsed = json.loads(output)
    assert 'exc' in parsed
    assert 'boom' in parsed['exc']
