from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class WebAppUrl:
    value: str | None = None

    def get(self) -> str | None:
        return self.value

    def set(self, value: str) -> None:
        self.value = value.rstrip("/")
