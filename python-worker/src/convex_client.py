"""Convex HTTP client for the Python worker."""

import os
import httpx


class ConvexWorkerClient:
    """Talks to Convex via the HTTP actions defined in http.ts."""

    def __init__(self) -> None:
        self.base_url = os.environ["CONVEX_HTTP_URL"].rstrip("/")
        self.api_key = os.environ["WORKER_API_KEY"]
        self._client = httpx.Client(timeout=30)

    def _headers(self) -> dict[str, str]:
        return {"X-Worker-Key": self.api_key, "Content-Type": "application/json"}

    def update_state(self, recording_id: str, state: str, **metadata: object) -> None:
        """Update a recording's state and optional metadata fields."""
        payload: dict[str, object] = {
            "recordingId": recording_id,
            "state": state,
        }
        for key, value in metadata.items():
            if value is not None:
                payload[key] = value

        resp = self._client.post(
            f"{self.base_url}/worker/updateState",
            json=payload,
            headers=self._headers(),
        )
        resp.raise_for_status()

    def store_riffs(self, recording_id: str, riffs: list[dict]) -> None:
        """Batch insert riffs for a recording."""
        resp = self._client.post(
            f"{self.base_url}/worker/storeRiffs",
            json={"recordingId": recording_id, "riffs": riffs},
            headers=self._headers(),
        )
        resp.raise_for_status()
