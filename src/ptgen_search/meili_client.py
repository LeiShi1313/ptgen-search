from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx


class MeiliError(RuntimeError):
    pass


class MeiliClient:
    def __init__(self, url: str, api_key: str, timeout: float = 60.0) -> None:
        self.url = url.rstrip("/")
        self.client = httpx.Client(
            base_url=self.url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise MeiliError(f"{method} {path} transport failed: {exc}") from exc
        if response.status_code >= 400:
            raise MeiliError(f"{method} {path} failed: {response.status_code} {response.text}")
        if response.content:
            return response.json()
        return None

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/health")

    def index_stats(self, index_name: str) -> dict[str, Any]:
        return self.request("GET", f"/indexes/{index_name}/stats")

    def search(self, index_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/indexes/{index_name}/search", json=payload)

    def document(self, index_name: str, document_id: str) -> dict[str, Any]:
        encoded_id = quote(document_id, safe="")
        return self.request("GET", f"/indexes/{index_name}/documents/{encoded_id}")

    def create_index(self, index_name: str, primary_key: str = "id") -> int:
        task = self.request("POST", "/indexes", json={"uid": index_name, "primaryKey": primary_key})
        return int(task["taskUid"])

    def delete_index(self, index_name: str) -> int | None:
        response = self.client.delete(f"/indexes/{index_name}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise MeiliError(f"DELETE /indexes/{index_name} failed: {response.status_code} {response.text}")
        return int(response.json()["taskUid"])

    def update_settings(self, index_name: str, settings: dict[str, Any]) -> int:
        task = self.request("PATCH", f"/indexes/{index_name}/settings", json=settings)
        return int(task["taskUid"])

    def add_documents(self, index_name: str, docs: list[dict[str, Any]]) -> int:
        task = self.request(
            "POST",
            f"/indexes/{index_name}/documents",
            params={"primaryKey": "id"},
            json=docs,
        )
        return int(task["taskUid"])

    def swap_indexes(self, first: str, second: str) -> int:
        task = self.request("POST", "/swap-indexes", json=[{"indexes": [first, second]}])
        return int(task["taskUid"])

    def wait_task(self, task_uid: int | None, timeout_seconds: int = 600) -> dict[str, Any] | None:
        if task_uid is None:
            return None
        deadline = time.monotonic() + timeout_seconds
        while True:
            task = self.request("GET", f"/tasks/{task_uid}")
            if task["status"] in {"succeeded", "failed", "canceled"}:
                if task["status"] != "succeeded":
                    raise MeiliError(f"Task {task_uid} ended as {task['status']}: {task}")
                return task
            if time.monotonic() >= deadline:
                raise MeiliError(f"Timed out waiting for Meilisearch task {task_uid}")
            time.sleep(0.5)
