from __future__ import annotations

import hashlib
import unittest

from orchestrator.naming import (
    STANDARD_ROLES,
    derive_requester_role,
    expected_agent_name,
    hash16_from_repository_identity,
    project_slug_from_repository_identity,
)

_SAMPLE_UUID = "550e8400-e29b-41d4-a716-446655440000"


class NamingTests(unittest.TestCase):
    def test_pattern_a_hash16(self) -> None:
        hash16 = hash16_from_repository_identity(_SAMPLE_UUID)
        expected = hashlib.sha256(_SAMPLE_UUID.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(hash16, expected)
        self.assertEqual(len(hash16), 16)

    def test_pattern_a_all_standard_roles(self) -> None:
        hash16 = hash16_from_repository_identity(_SAMPLE_UUID)
        for role in STANDARD_ROLES:
            name = expected_agent_name(_SAMPLE_UUID, role)
            expected = f"r-{hash16}-{role}"
            self.assertEqual(name, expected)
            self.assertLessEqual(len(name), 32)
            self.assertTrue(name.startswith("r-"))
            self.assertTrue(name.endswith(f"-{role}"))

    def test_orchestrator_max_length_31_chars(self) -> None:
        name = expected_agent_name(_SAMPLE_UUID, "orchestrator")
        self.assertEqual(len(name), 31)
        self.assertLessEqual(len(name), 32)

    def test_derive_requester_role_unique(self) -> None:
        for role in STANDARD_ROLES:
            name = expected_agent_name(_SAMPLE_UUID, role)
            self.assertEqual(derive_requester_role(_SAMPLE_UUID, name), role)

    def test_derive_requester_role_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            derive_requester_role(_SAMPLE_UUID, "unknown-agent")

    def test_derive_requester_role_rejects_different_uuid(self) -> None:
        name1 = expected_agent_name(_SAMPLE_UUID, "design")
        other_uuid = "a0000000-0000-4000-8000-000000000000"
        with self.assertRaises(ValueError):
            derive_requester_role(other_uuid, name1)

    def test_unsupported_role_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            expected_agent_name(_SAMPLE_UUID, "tester")

    def test_legacy_slug_helper(self) -> None:
        self.assertEqual(
            project_slug_from_repository_identity("git.example/repo"),
            "git-example-repo",
        )
        slug = project_slug_from_repository_identity("0123/foo")
        self.assertTrue(slug.startswith("r-"))


if __name__ == "__main__":
    unittest.main()
