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

    def store_match(
        self, riff_a_id: str, riff_b_id: str, score: float, breakdown: dict
    ) -> None:
        """Store a riff match result."""
        resp = self._client.post(
            f"{self.base_url}/worker/storeMatch",
            json={
                "riffAId": riff_a_id,
                "riffBId": riff_b_id,
                "score": score,
                "breakdown": breakdown,
            },
            headers=self._headers(),
        )
        resp.raise_for_status()

    def get_all_riffs(self) -> list[dict]:
        """Fetch all riffs from Convex."""
        resp = self._client.post(
            f"{self.base_url}/worker/getAllRiffs",
            json={},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()["riffs"]

    def get_riffs_for_recording(self, recording_id: str) -> list[dict]:
        """Fetch riffs for a specific recording."""
        resp = self._client.post(
            f"{self.base_url}/worker/getRiffsForRecording",
            json={"recordingId": recording_id},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()["riffs"]
