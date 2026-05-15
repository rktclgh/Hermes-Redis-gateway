from __future__ import annotations

from hermes_redis_gateway.slot_lease import SlotLeaseManager


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def eval(self, script: str, key_count: int, key: str, token: str, *args: object) -> int:
        if "DEL" in script:
            if self.values.get(key) == token:
                del self.values[key]
                return 1
            return 0
        if "EXPIRE" in script:
            return 1 if self.values.get(key) == token else 0
        return 0

    def exists(self, key: str) -> int:
        return int(key in self.values)


class Settings:
    slot_acquire_timeout_seconds = 1
    slot_count = 2
    slot_lease_seconds = 60
    slot_profile_prefix = "profile"
    slot_prefix = "slot:"


def test_slot_release_requires_matching_token() -> None:
    redis = FakeRedis()
    manager = SlotLeaseManager(redis, Settings())
    lease = manager.acquire("job-a")
    assert lease is not None

    redis.values["slot:1"] = "other-token"
    manager.release(lease)

    assert redis.values["slot:1"] == "other-token"


def test_slot_snapshot_counts_used_slots() -> None:
    redis = FakeRedis()
    manager = SlotLeaseManager(redis, Settings())
    assert manager.snapshot() == {"slots": 2, "used": 0, "available": 2}

    assert manager.acquire("job-a") is not None
    assert manager.snapshot() == {"slots": 2, "used": 1, "available": 1}
