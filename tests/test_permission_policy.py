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

    def test_silence_or_unrequested_state_blocks(self):
        for state in ("UNREQUESTED", "DRAFT", "REQUESTED", "REJECTED", "EXPIRED", "REVOKED"):
            with self.subTest(state=state):
                result = evaluate_publish_permission(approved(status=state), "youtube", now=NOW)
                self.assertFalse(result.allowed)

    def test_expired_permission_blocks_at_boundary(self):
        permission = approved(expires_at=NOW)
        result = evaluate_publish_permission(permission, "youtube", now=NOW)
        self.assertEqual(result.reason, "permission_expired")

    def test_revoked_permission_blocks(self):
        permission = approved(revoked_at=NOW - timedelta(seconds=1))
        result = evaluate_publish_permission(permission, "youtube", now=NOW)
        self.assertEqual(result.reason, "permission_revoked")

    def test_platform_scope_is_enforced(self):
        result = evaluate_publish_permission(approved(), "tiktok", now=NOW)
        self.assertEqual(result.reason, "platform_not_authorized")

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
        result = evaluate_publish_permission(
            approved(), "youtube", now=datetime(2026, 11, 14, 12, 0)
        )
        self.assertEqual(result.reason, "clock_must_be_timezone_aware")


if __name__ == "__main__":
    unittest.main()
