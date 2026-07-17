"""Wire-shape pin for `encode_stream_chunk` (FastAPI chat SSE boundary).

The user-reported bug: ggcoder chat rendered raw
``{"type":"text_delta","text":"..."}`` JSON inside the assistant bubble.

Wire path:
  harness -> daemon websocket -> agent run-events channel
  -> `_handle_harness_event` -> `publish_conversation_event(token_payload(...))`
  -> `iter_subscription` -> `encode_stream_chunk` -> `data: <json>\n\n` SSE line
  -> `readSSE` on the chat UI side -> `parseAssistantStreamEvent` -> chat bubble.

The encoder at this boundary is a pure pass-through: it JSON-serializes
whatever `data` Python object it receives. The bubble's content is therefore
determined entirely by what shape upstream producers hand it. To keep the
chat bubble readable, *string-shaped* `data` must reach the wire as a JSON
string; if anyone upstream hands it a *dict*, the wire round-trips it as a
nested object -- and the chat SDK then stringifies that dict via
JSON.stringify, producing the literal `{"type":"text_delta","text":"..."}`
the user saw.

These tests pin both halves of that contract, plus the multi-chunk and
message-event variants. The fix for the user's bug lives in the harness
side (`lemma-cli/.../runner.py` adding GG_CODER to the streaming-dispatch
set); this file is the downstream tail of the pin.
"""
from __future__ import annotations

import json
from uuid import uuid4

from app.modules.agent.api.controllers.shared import encode_stream_chunk


_RAW_NDJSON_SIGNATURE = '"type":"text_delta"'


def _parse_sse_frame(frame: str) -> dict:
    """Parse one SSE data: frame back into a Python dict."""
    assert frame.startswith("data: "), f"frame does not start with `data: `: {frame!r}"
    payload = frame[len("data: "):].rstrip("\n")
    return json.loads(payload)


def test_encode_stream_chunk_with_string_data_roundtrips_as_json_string():
    """Happy path: a `token` event with a plain string `data` arrives at the
    wire as a JSON string. The chat SDK sees `token.data === "I see..."` and
    concatenates it into the assistant bubble."""
    frame = encode_stream_chunk(
        event_type="token",
        data="I see an empty message.",
        agent_run_id=uuid4(),
        kind="text",
    )

    decoded = _parse_sse_frame(frame)
    assert decoded["type"] == "token"
    assert decoded["kind"] == "text"
    assert decoded["data"] == "I see an empty message."
    assert isinstance(decoded["data"], str), (
        "token.data must arrive as a JSON string; if it doesn't, the chat "
        f"SDK stringifies it and the user sees JSON. Got: {decoded['data']!r}"
    )
    assert _RAW_NDJSON_SIGNATURE not in frame, (
        f"raw NDJSON leaked into the SSE wire frame: {frame!r}"
    )


def test_encode_stream_chunk_with_dict_data_roundtrips_as_json_object():
    """Regression detector: if a caller (typically a harness) hands a dict
    for `data`, the encoder preserves it as a nested JSON object -- NOT a
    stringified envelope. The chat SDK's `JSON.stringify(token.data)`
    fallback then renders that nested object as text in the bubble, which
    is the symptom users reported."""
    frame = encode_stream_chunk(
        event_type="token",
        data={"type": "text_delta", "text": "hello"},
        agent_run_id=uuid4(),
        kind="text",
    )

    decoded = _parse_sse_frame(frame)
    assert isinstance(decoded["data"], dict), (
        f"dict-shaped data must roundtrip as an object, got: {decoded['data']!r}"
    )
    # Pin the actual symptom: the chat SDK stringifies this object,
    # producing the `{"type":"text_delta","text":"..."}` blob the user saw.
    assert decoded["data"] == {"type": "text_delta", "text": "hello"}


def test_encode_stream_chunk_token_iterations_concatenate_to_plain_prose():
    """Multi-chunk token stream: each chunk's `data` field stays a string in
    the wire payload. The chat UI concatenates these strings to render the
    assistant message incrementally -- which is the user-facing 'incremental
    rendering' behavior we must preserve."""
    chunks = ["I see", " an empty", " message.", " What would you like me to do?"]
    decoded_chunks = []
    for chunk in chunks:
        frame = encode_stream_chunk(
            event_type="token",
            data=chunk,
            agent_run_id=uuid4(),
            kind="text",
        )
        decoded = _parse_sse_frame(frame)
        decoded_chunks.append(decoded["data"])

    concatenated = "".join(decoded_chunks)
    assert concatenated == "I see an empty message. What would you like me to do?"
    assert _RAW_NDJSON_SIGNATURE not in concatenated
