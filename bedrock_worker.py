import json
import os
import threading

import boto3
from PySide6.QtCore import QObject, Signal
from botocore.config import Config


DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


class BedrockWorker(QObject):
    """
    Runs Bedrock calls off the UI thread and streams partial output.

    Design notes:
    - Uses a request_id so the UI can ignore late results from cancelled/timeouts.
    - "Cancel" is cooperative: we can't reliably abort an in-flight HTTPS request,
      but we stop emitting updates and the UI immediately resets.
    - Reuses the boto3 Bedrock client to reduce per-click overhead.
    """

    partial = Signal(int, str)
    finished = Signal(int, str)

    def __init__(self, model_id: str = DEFAULT_MODEL_ID, region_name: str = "us-east-2"):
        super().__init__()
        self._model_id = model_id
        self._region_name = region_name

        self._lock = threading.Lock()
        self._active_request_id = 0
        self._cancelled_request_ids = set()
        self._client = None

    def set_active(self, request_id: int) -> None:
        with self._lock:
            self._active_request_id = request_id

    def cancel_active(self) -> int:
        with self._lock:
            req_id = self._active_request_id
            if req_id:
                # Mark cancelled so streaming loop stops emitting updates.
                self._cancelled_request_ids.add(req_id)
                self._active_request_id = 0
            return req_id

    def _clear_cancelled(self, request_id: int) -> None:
        with self._lock:
            self._cancelled_request_ids.discard(request_id)

    def _get_client(self):
        with self._lock:
            if self._client is None:
                # Explicit timeouts prevent "wait forever" if the network/service stalls.
                self._client = boto3.client(
                    "bedrock-runtime",
                    region_name=self._region_name,
                    config=Config(
                        connect_timeout=5,
                        read_timeout=25,
                        retries={"max_attempts": 1, "mode": "standard"},
                    ),
                )
            return self._client

    def invoke_ai(self, request_id: int, race_json: str) -> None:
        def run():
            try:
                token = os.getenv("IRACING_BEDROCK_TOKEN")
                if not token:
                    msg = "Error: IRACING_BEDROCK_TOKEN not found in environment."
                    print(f"[ERROR] {msg}")
                    self.finished.emit(request_id, msg)
                    return

                with self._lock:
                    if request_id in self._cancelled_request_ids:
                        return

                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token
                client = self._get_client()

                prompt = (
                    "You are a Lead Race Engineer. Analyze the telemetry and race status. "
                    "Primary Goal: Optimize track position vs fuel/tire life. "
                    "If 'flags' indicates a Caution, prioritize whether to 'Pit Now' or 'Stay Out' based on field behavior and fuel. "
                    "If Green, compare User lap fall-off against the field. "
                    "Output: 10 words max. Be decisive (e.g., 'Caution out, Pit Now for tires/fuel' or 'Green. Stay out, +5 laps'). "
                    f"Data: {race_json}"
                )

                body = json.dumps(
                    {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 100,
                        "messages": [{"role": "user", "content": prompt}],
                    }
                )

                print(f"[DEBUG] Sending data to AI: {race_json[:100]}...")

                response = client.invoke_model_with_response_stream(body=body, modelId=self._model_id)
                stream = response.get("body")

                advice_parts = []

                def is_cancelled() -> bool:
                    with self._lock:
                        return request_id in self._cancelled_request_ids

                if stream is not None:
                    for event in stream:
                        if is_cancelled():
                            self._clear_cancelled(request_id)
                            return
                        if not isinstance(event, dict) or "chunk" not in event:
                            continue
                        chunk = event.get("chunk") or {}
                        b = chunk.get("bytes")
                        if not b:
                            continue
                        try:
                            payload = json.loads(b.decode("utf-8"))
                        except Exception:
                            continue

                        # Bedrock/Anthropic streaming sends many event types; we only
                        # extract incremental text deltas to build the user-visible advice.
                        text = None
                        if isinstance(payload, dict):
                            delta = payload.get("delta")
                            if isinstance(delta, dict):
                                text = delta.get("text")
                            if text is None and "text" in payload:
                                text = payload.get("text")

                        if text:
                            advice_parts.append(text)
                            current = "".join(advice_parts).strip()
                            if current:
                                self.partial.emit(request_id, current)

                advice = "".join(advice_parts).strip()
                if not advice:
                    advice = "AI Error: Empty response."

                print(f"[SUCCESS] AI Advice: {advice}")
                with self._lock:
                    if request_id in self._cancelled_request_ids:
                        self._clear_cancelled(request_id)
                        return

                self.finished.emit(request_id, advice)
                self._clear_cancelled(request_id)
            except Exception as e:
                print(f"[ERROR] {str(e)}")
                with self._lock:
                    if request_id in self._cancelled_request_ids:
                        self._clear_cancelled(request_id)
                        return
                self.finished.emit(request_id, f"AI Error: {str(e)}")
                self._clear_cancelled(request_id)

        threading.Thread(target=run, daemon=True).start()

