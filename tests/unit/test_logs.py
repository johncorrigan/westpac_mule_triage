import io
import json
import logging
from collections.abc import Iterator

import pytest

from mule_triage.logs import JsonLines, log_event

LOGGER = "mule_triage.tests"


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    """What a logger under the package writes through `JsonLines`."""
    written = io.StringIO()
    handler = logging.StreamHandler(written)
    handler.setFormatter(JsonLines())
    logger = logging.getLogger(LOGGER)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield written
    logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)


def test_an_event_is_one_line_of_json_naming_the_event_and_the_run(
    stream: io.StringIO,
) -> None:
    log_event(LOGGER, logging.INFO, "run_stored", 7, payments={"received": 35})

    (line,) = stream.getvalue().splitlines()
    record = json.loads(line)
    assert record["event"] == "run_stored"
    assert record["run_id"] == 7
    assert record["payments"] == {"received": 35}
    assert (record["level"], record["logger"]) == ("INFO", LOGGER)
    assert record["time"].endswith("Z")


def test_a_field_holding_a_newline_stays_on_one_line(stream: io.StringIO) -> None:
    log_event(LOGGER, logging.ERROR, "run_finished", None, error="one\ntwo")

    (line,) = stream.getvalue().splitlines()
    assert json.loads(line)["error"] == "one\ntwo"


def test_a_record_logged_without_an_event_still_names_a_run(
    stream: io.StringIO,
) -> None:
    logging.getLogger(LOGGER).warning("plain text")

    record = json.loads(stream.getvalue())
    assert (record["event"], record["run_id"]) == ("plain text", None)
