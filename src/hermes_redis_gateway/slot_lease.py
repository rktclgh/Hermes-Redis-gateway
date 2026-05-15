from __future__ import annotations

from dataclasses import dataclass
import time

import redis

from .config import Settings


@dataclass(frozen=True)
class SlotLease:
    index: int
    name: str
    profile: str
    token: str


class SlotLeaseManager:
    def __init__(self, client: redis.Redis, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    def acquire(self, job_id: str) -> SlotLease | None:
        deadline = time.monotonic() + self.settings.slot_acquire_timeout_seconds
        token = f"{job_id}:{time.time_ns()}"
        while time.monotonic() < deadline:
            for index in range(1, self.settings.slot_count + 1):
                key = self._key(index)
                if self.client.set(key, token, nx=True, ex=self.settings.slot_lease_seconds):
                    return SlotLease(
                        index=index,
                        name=f"slot-{index}",
                        profile=f"{self.settings.slot_profile_prefix}-{index}",
                        token=token,
                    )
            time.sleep(0.2)
        return None

    def refresh(self, lease: SlotLease) -> bool:
        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("EXPIRE", KEYS[1], ARGV[2])
        end
        return 0
        """
        return bool(self.client.eval(script, 1, self._key(lease.index), lease.token, self.settings.slot_lease_seconds))

    def owns(self, lease: SlotLease) -> bool:
        current = self.client.get(self._key(lease.index))
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        return current == lease.token

    def release(self, lease: SlotLease) -> None:
        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        end
        return 0
        """
        self.client.eval(script, 1, self._key(lease.index), lease.token)

    def snapshot(self) -> dict[str, int]:
        used = 0
        for index in range(1, self.settings.slot_count + 1):
            if self.client.exists(self._key(index)):
                used += 1
        return {
            "slots": self.settings.slot_count,
            "used": used,
            "available": self.settings.slot_count - used,
        }

    def _key(self, index: int) -> str:
        return f"{self.settings.slot_prefix}{index}"
