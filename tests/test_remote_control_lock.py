from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ichannel.orchestrator import RemoteControlLock  # noqa: E402


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RemoteControlLockTests(unittest.TestCase):
    def test_claim_blocks_other_users_until_idle_timeout(self) -> None:
        clock = MutableClock()
        lock = RemoteControlLock(timeout_seconds=300, clock=clock)

        first = lock.claim_or_refresh(user_id=1, username="alice", guild_id=10)
        self.assertTrue(first.allowed)

        clock.advance(299)
        blocked = lock.claim_or_refresh(user_id=2, username="bob", guild_id=10)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.holder.username, "alice")
        self.assertEqual(blocked.remaining_seconds, 1)

        clock.advance(1)
        second = lock.claim_or_refresh(user_id=2, username="bob", guild_id=10)
        self.assertTrue(second.allowed)
        self.assertEqual(second.holder.username, "bob")

    def test_owner_activity_extends_lock(self) -> None:
        clock = MutableClock()
        lock = RemoteControlLock(timeout_seconds=300, clock=clock)

        self.assertTrue(lock.claim_or_refresh(user_id=1, username="alice", guild_id=10).allowed)
        clock.advance(200)
        self.assertTrue(lock.claim_or_refresh(user_id=1, username="alice", guild_id=10).allowed)

        clock.advance(101)
        blocked = lock.claim_or_refresh(user_id=2, username="bob", guild_id=10)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.remaining_seconds, 199)

    def test_remaining_seconds_reports_current_owner_lease(self) -> None:
        clock = MutableClock()
        lock = RemoteControlLock(timeout_seconds=300, clock=clock)

        self.assertIsNone(lock.remaining_seconds(user_id=1))
        self.assertTrue(lock.claim_or_refresh(user_id=1, username="alice", guild_id=10).allowed)
        clock.advance(42)

        self.assertEqual(lock.remaining_seconds(user_id=1), 258)
        self.assertIsNone(lock.remaining_seconds(user_id=2))

    def test_previous_owner_can_reclaim_expired_lock(self) -> None:
        clock = MutableClock()
        lock = RemoteControlLock(timeout_seconds=300, clock=clock)

        self.assertTrue(lock.claim_or_refresh(user_id=1, username="alice", guild_id=10).allowed)
        clock.advance(300)

        reclaimed = lock.claim_or_refresh(user_id=1, username="alice", guild_id=10)
        self.assertTrue(reclaimed.allowed)
        self.assertEqual(reclaimed.holder.user_id, 1)

    def test_release_allows_new_owner_before_idle_timeout(self) -> None:
        clock = MutableClock()
        lock = RemoteControlLock(timeout_seconds=300, clock=clock)

        self.assertTrue(lock.claim_or_refresh(user_id=1, username="alice", guild_id=10).allowed)
        clock.advance(1)

        lock.release()
        claimed = lock.claim_or_refresh(user_id=2, username="bob", guild_id=10)

        self.assertTrue(claimed.allowed)
        self.assertEqual(claimed.holder.username, "bob")


if __name__ == "__main__":
    unittest.main()
