"""The model clients: off, replay and live, each behind `ModelClient.draft`.

A recording is filed under its key: the model, the prompt version and the case's facts hash,
which `evidence.facts_sha256` takes over canonical JSON. It holds the reply and the model's
digest, and no times or durations, so the same reply always gives the same bytes. The live
client talks to Ollama's /api/chat through urllib, with no proxy, so the case's facts go only
to the endpoint named. After one failure it reports the model unavailable for the rest of
the run, rather than waiting out the timeout again for every alert.
"""

import http.client
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, ValidationError

from mule_triage.contracts import NarrativeOutcome
from mule_triage.errors import RecordingMissing
from mule_triage.narrative.prompt import Prompt, model_options, reply_schema

MAX_RESPONSE_BYTES = 1_000_000
UNSAFE_IN_FILE_NAME = re.compile(r"[^A-Za-z0-9._-]")


class DraftSource(StrEnum):
    LIVE = "live"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class ModelLabel:
    model: str
    digest: str
    source: DraftSource


@dataclass(frozen=True, slots=True)
class Drafted:
    """`latency_seconds` is None for a replayed reply."""

    reply: str
    label: ModelLabel
    latency_seconds: float | None


@dataclass(frozen=True, slots=True)
class NotDrafted:
    outcome: NarrativeOutcome
    detail: str


DraftResult: TypeAlias = Drafted | NotDrafted


class ModelClient(Protocol):
    def draft(self, prompt: Prompt, facts_sha256: str) -> DraftResult: ...


@dataclass(frozen=True, slots=True)
class RecordingKey:
    model: str
    prompt_version: str
    facts_sha256: str

    @property
    def file_name(self) -> str:
        model = UNSAFE_IN_FILE_NAME.sub("-", self.model)
        return f"{model}_{self.prompt_version}_{self.facts_sha256}.json"


class Recording(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    model: str
    digest: str
    prompt_version: str
    facts_sha256: str
    reply: str

    @property
    def key(self) -> RecordingKey:
        return RecordingKey(self.model, self.prompt_version, self.facts_sha256)


def write_recording(folder: Path, recording: Recording) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / recording.key.file_name
    text = json.dumps(
        recording.model_dump(), indent=2, sort_keys=True, ensure_ascii=True
    )
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
    return path


def read_recording(folder: Path, key: RecordingKey) -> Recording:
    """Raises RecordingMissing when there's no file, and ValueError when the file isn't a
    recording of this key."""
    path = folder / key.file_name
    if not path.is_file():
        raise RecordingMissing(f"no recording {key.file_name} in {folder}")
    recording = Recording.model_validate_json(path.read_text(encoding="utf-8"))
    if recording.key != key:
        raise ValueError(f"{key.file_name} holds the recording of another key")
    return recording


@dataclass(frozen=True, slots=True)
class OffClient:
    def draft(self, prompt: Prompt, facts_sha256: str) -> DraftResult:
        return NotDrafted(NarrativeOutcome.AI_OFF, "AI drafting is off (--ai off)")


@dataclass(frozen=True, slots=True)
class ReplayClient:
    folder: Path
    model: str

    def draft(self, prompt: Prompt, facts_sha256: str) -> DraftResult:
        key = RecordingKey(self.model, prompt.version, facts_sha256)
        try:
            recording = read_recording(self.folder, key)
        except RecordingMissing as missing:
            return NotDrafted(NarrativeOutcome.NO_RECORDING, str(missing))
        except ValueError:
            detail = f"recording {key.file_name} in {self.folder} is unreadable"
            return NotDrafted(NarrativeOutcome.NO_RECORDING, detail)
        label = ModelLabel(recording.model, recording.digest, DraftSource.REPLAY)
        return Drafted(recording.reply, label, latency_seconds=None)


# Ollama's replies carry more fields than these, and add new ones, so the rest are ignored.
class OllamaModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    digest: str


class OllamaTags(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    models: tuple[OllamaModel, ...]


class OllamaMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    content: str


class OllamaChat(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    message: OllamaMessage


class LiveClient:
    def __init__(
        self,
        endpoint: str,
        model: str,
        timeout_seconds: float,
        record_to: Path | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._record_to = record_to
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._digest: str | None = None
        self._unavailable: str | None = None

    def draft(self, prompt: Prompt, facts_sha256: str) -> DraftResult:
        """Only failing to reach the model, or a server that isn't serving it, makes it
        unavailable for the rest of the run; a bad reply is rejected for its case alone. A
        recording that can't be written raises OSError: it isn't the model's failure."""
        if self._unavailable is not None:
            return NotDrafted(NarrativeOutcome.AI_UNAVAILABLE, self._unavailable)
        try:
            digest = self._model_digest()
        except (OSError, http.client.HTTPException, ValueError) as error:
            return self._unavailable_now(unavailable_reason(error))
        if digest is None:
            return self._unavailable_now(f"doesn't have the model {self._model}")
        try:
            started = time.perf_counter()
            body = self._request("/api/chat", self._chat_request(prompt))
            latency = time.perf_counter() - started
        except (OSError, http.client.HTTPException) as error:
            return self._unavailable_now(unavailable_reason(error))
        reply = chat_content(body)
        if reply is None:
            detail = (
                "its reply wasn't an Ollama chat reply"
                f" of at most {MAX_RESPONSE_BYTES} bytes"
            )
            return NotDrafted(NarrativeOutcome.REJECTED, detail)
        if self._record_to is not None:
            recording = Recording(
                model=self._model,
                digest=digest,
                prompt_version=prompt.version,
                facts_sha256=facts_sha256,
                reply=reply,
            )
            write_recording(self._record_to, recording)
        label = ModelLabel(self._model, digest, DraftSource.LIVE)
        return Drafted(reply, label, latency)

    def _unavailable_now(self, reason: str) -> NotDrafted:
        self._unavailable = f"{self._endpoint} {reason}"
        return NotDrafted(NarrativeOutcome.AI_UNAVAILABLE, self._unavailable)

    def _model_digest(self) -> str | None:
        if self._digest is None:
            data = self._request("/api/tags", None)
            if len(data) > MAX_RESPONSE_BYTES:
                raise ValueError("the model list is too long")
            tags = OllamaTags.model_validate_json(data)
            names = {self._model, f"{self._model}:latest"}
            found = [each.digest for each in tags.models if each.name in names]
            self._digest = found[0] if found else None
        return self._digest

    def _chat_request(self, prompt: Prompt) -> dict[str, object]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt.instructions},
                {"role": "user", "content": prompt.data},
            ],
            "stream": False,
            "format": reply_schema(),
            "options": model_options(),
        }

    def _request(self, path: str, body: dict[str, object] | None) -> bytes:
        """At most one byte over MAX_RESPONSE_BYTES, so the caller can tell it's too long."""
        request = urllib.request.Request(
            self._endpoint + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="GET" if body is None else "POST",
        )
        with self._opener.open(request, timeout=self._timeout) as response:
            data: bytes = response.read(MAX_RESPONSE_BYTES + 1)
        return data


def chat_content(body: bytes) -> str | None:
    if len(body) > MAX_RESPONSE_BYTES:
        return None
    try:
        return OllamaChat.model_validate_json(body).message.content
    except ValidationError:
        return None


def unavailable_reason(error: Exception) -> str:
    """A fixed message for each kind of failure, because an error's own text can quote the
    server, and the reason reaches the reviewer."""
    if isinstance(error, urllib.error.HTTPError):
        return f"answered with HTTP status {error.code}"
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, TimeoutError):
        return "didn't answer in time"
    if isinstance(cause, ConnectionRefusedError):
        return "refused the connection"
    if isinstance(error, OSError):
        return "couldn't be reached"
    if isinstance(error, http.client.HTTPException):
        return "broke off its reply"
    return "didn't answer as Ollama does"
