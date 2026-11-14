from datetime import datetime, timedelta, timezone
import unittest

from platform_core.permission_policy import Permission, evaluate_publish_permission


NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def approved(**overrides):
    values = {
        "status": "APPROVED",
        "allowed_platforms": frozenset({"youtube", "instagram"}),
        "approved_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=1),
        "revoked_at": None,
        "commercial_use_allowed": True,
        "editing_allowed": True,
    }
    values.update(overrides)
    return Permission(**values)


class PermissionPolicyTests(unittest.TestCase):
    def test_valid_approval_allows_authorized_platform(self):
        result = evaluate_publish_permission(approved(), "youtube", now=NOW)
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "permission_valid")

    def test_missing_record_blocks(self):
        self.assertFalse(evaluate_publish_permission(None, "youtube", now=NOW).allowed)

    def test_non_approved_states_block(self):
        for state in ("UNREQUESTED", "DRAFT", "REQUESTED", "REJECTED", "EXPIRED", "REVOKED"):
            with self.subTest(state=state):
                result = evaluate_publish_permission(approved(status=state), "youtube", now=NOW)
                self.assertFalse(result.allowed)

    def test_invalid_state_blocks(self):
        self.assertEqual(
            evaluate_publish_permission(approved(status="APPROVE"), "youtube", now=NOW).reason,
            "permission_state_invalid",
        )

    def test_missing_or_naive_approval_timestamp_blocks(self):
        self.assertFalse(evaluate_publish_permission(
            approved(approved_at=None), "youtube", now=NOW
        ).allowed)
        self.assertEqual(evaluate_publish_permission(
            approved(approved_at=datetime(2026, 11, 13)), "youtube", now=NOW
        ).reason, "approval_timestamp_missing_or_invalid")

    def test_future_approval_timestamp_blocks(self):
        self.assertEqual(evaluate_publish_permission(
            approved(approved_at=NOW + timedelta(seconds=1)), "youtube", now=NOW
        ).reason, "approval_timestamp_in_future")

    def test_expired_permission_blocks_at_boundary(self):
        self.assertEqual(
            evaluate_publish_permission(approved(expires_at=NOW), "youtube", now=NOW).reason,
            "permission_expired",
        )

    def test_naive_expiry_blocks(self):
        self.assertEqual(evaluate_publish_permission(
            approved(expires_at=datetime(2026, 11, 15)), "youtube", now=NOW
        ).reason, "expiry_must_be_timezone_aware")

    def test_revoked_permission_blocks(self):
        self.assertEqual(evaluate_publish_permission(
            approved(revoked_at=NOW - timedelta(seconds=1)), "youtube", now=NOW
        ).reason, "permission_revoked")

    def test_naive_revocation_timestamp_blocks(self):
        self.assertEqual(evaluate_publish_permission(
            approved(revoked_at=datetime(2026, 11, 13)), "youtube", now=NOW
        ).reason, "revocation_timestamp_invalid")

    def test_platform_scope_is_enforced(self):
        self.assertEqual(
            evaluate_publish_permission(approved(), "tiktok", now=NOW).reason,
            "platform_not_authorized",
        )

    def test_empty_platform_blocks(self):
        self.assertFalse(evaluate_publish_permission(approved(), "", now=NOW).allowed)

    def test_commercial_and_editing_scopes_are_enforced(self):
        self.assertFalse(evaluate_publish_permission(
            approved(commercial_use_allowed=False), "youtube",
            now=NOW, commercial_use=True
        ).allowed)
        self.assertFalse(evaluate_publish_permission(
            approved(editing_allowed=False), "youtube",
            now=NOW, editing_required=True
        ).allowed)

    def test_naive_clock_is_rejected(self):
        self.assertEqual(evaluate_publish_permission(
            approved(), "youtube", now=datetime(2026, 11, 14, 12, 0)
        ).reason, "clock_must_be_timezone_aware")


if __name__ == "__main__":
    unittest.main()
