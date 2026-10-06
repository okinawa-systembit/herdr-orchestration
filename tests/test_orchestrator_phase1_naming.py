from __future__ import annotations

import hashlib
import unittest

from orchestrator.naming import (
    derive_requester_role,
    expected_agent_name,
    project_slug_from_repository_identity,
)


class NamingTests(unittest.TestCase):
    def test_example_identity_slug(self) -> None:
        self.assertEqual(
            project_slug_from_repository_identity("git.example/repo"),
            "git-example-repo",
        )
        self.assertEqual(
            expected_agent_name("git.example/repo", "reviewer"),
            "git-example-repo-reviewer",
        )

    def test_standard_roles_for_example_repo(self) -> None:
        identity = "git.example/repo"
        self.assertEqual(
            expected_agent_name(identity, "design"),
            "git-example-repo-design",
        )
        self.assertEqual(
            expected_agent_name(identity, "implementer"),
            "git-example-repo-implementer",
        )

    def test_numeric_slug_gets_r_prefix(self) -> None:
        slug = project_slug_from_repository_identity("0123/foo")
        self.assertTrue(slug.startswith("r-"))

    def test_derive_requester_role_unique(self) -> None:
        identity = "git.example/repo"
        name = expected_agent_name(identity, "design")
        self.assertEqual(derive_requester_role(identity, name), "design")

    def test_derive_requester_role_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            derive_requester_role("git.example/repo", "unknown-agent")

    def test_long_identity_uses_hash_form(self) -> None:
        identity = "very-long-host.example.com/org/some/deep/path/to/repository"
        name = expected_agent_name(identity, "reviewer")
        self.assertLessEqual(len(name), 32)
        self.assertTrue(name.endswith("-reviewer"))
        hash4 = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:4]
        self.assertIn(f"-{hash4}-reviewer", name)

    def test_candidate_length_32_uses_normal_form(self) -> None:
        # slug 23 + "-" + "reviewer"(8) = 32
        identity = "a.example/x"
        slug = project_slug_from_repository_identity(identity)
        candidate = f"{slug}-reviewer"
        self.assertLessEqual(len(candidate), 32)
        self.assertEqual(expected_agent_name(identity, "reviewer"), candidate)

    def test_candidate_length_33_switches_to_hash_form(self) -> None:
        identity = "aaaa.example/" + ("x" * 40)
        slug = project_slug_from_repository_identity(identity)
        self.assertGreater(len(f"{slug}-reviewer"), 32)
        name = expected_agent_name(identity, "reviewer")
        self.assertLessEqual(len(name), 32)
        self.assertNotEqual(name, f"{slug}-reviewer")

    def test_derive_requester_role_zero_matches_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            derive_requester_role("git.example/repo", "git-example-repo-tester")


if __name__ == "__main__":
    unittest.main()
