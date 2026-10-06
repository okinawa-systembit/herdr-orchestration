from __future__ import annotations

import unittest

from orchestrator.identity import RepositoryIdentityError, normalize_remote_url


class IdentityNormalizationTests(unittest.TestCase):
    def test_https_github(self) -> None:
        self.assertEqual(
            normalize_remote_url("https://github.com/org/repo"),
            "github.com/org/repo",
        )

    def test_https_strips_userinfo(self) -> None:
        self.assertEqual(
            normalize_remote_url("https://user:token@github.com/org/repo"),
            "github.com/org/repo",
        )

    def test_scp_matches_https(self) -> None:
        a = normalize_remote_url("git@github.com:org/repo")
        b = normalize_remote_url("https://github.com/org/repo")
        self.assertEqual(a, b)

    def test_ssh_default_port(self) -> None:
        self.assertEqual(
            normalize_remote_url("ssh://user@host/org/repo"),
            "host/org/repo",
        )

    def test_nonstandard_ssh_port(self) -> None:
        self.assertEqual(
            normalize_remote_url("ssh://host:2222/org/repo"),
            "host:2222/org/repo",
        )
        self.assertNotEqual(
            normalize_remote_url("ssh://host:2222/org/repo"),
            normalize_remote_url("ssh://host/org/repo"),
        )

    def test_https_nonstandard_port(self) -> None:
        self.assertEqual(
            normalize_remote_url("https://host:8443/org/repo"),
            "host:8443/org/repo",
        )
        self.assertEqual(
            normalize_remote_url("https://host/org/repo"),
            "host/org/repo",
        )

    def test_host_lowercase_path_case_preserved(self) -> None:
        self.assertEqual(
            normalize_remote_url("https://GitHub.com/org/Repo"),
            "github.com/org/Repo",
        )
        a = normalize_remote_url("https://github.com/org/Repo")
        b = normalize_remote_url("https://github.com/org/repo")
        self.assertNotEqual(a, b)

    def test_origin_multiple_urls_same_identity_ok(self) -> None:
        from orchestrator.identity import identity_from_origin_fetch_urls

        identity = identity_from_origin_fetch_urls(
            [
                "https://github.com/org/repo",
                "git@github.com:org/repo.git",
            ]
        )
        self.assertEqual(identity, "github.com/org/repo")

    def test_origin_multiple_urls_conflict(self) -> None:
        from orchestrator.identity import identity_from_origin_fetch_urls

        with self.assertRaises(RepositoryIdentityError):
            identity_from_origin_fetch_urls(
                [
                    "https://github.com/org/repo",
                    "https://gitlab.com/org/repo",
                ]
            )

    def test_rejects_http_scheme(self) -> None:
        with self.assertRaises(RepositoryIdentityError):
            normalize_remote_url("http://host/org/repo")

    def test_rejects_git_scheme(self) -> None:
        with self.assertRaises(RepositoryIdentityError):
            normalize_remote_url("git://host/org/repo")

    def test_rejects_bare_host_path(self) -> None:
        with self.assertRaises(RepositoryIdentityError):
            normalize_remote_url("host/org/repo")

    def test_rejects_https_empty_path(self) -> None:
        with self.assertRaises(RepositoryIdentityError):
            normalize_remote_url("https://host")

    def test_rejects_non_numeric_port(self) -> None:
        with self.assertRaises(RepositoryIdentityError):
            normalize_remote_url("https://host:abc/org/repo")

    def test_rejects_port_out_of_range(self) -> None:
        with self.assertRaises(RepositoryIdentityError):
            normalize_remote_url("https://host:99999/org/repo")

    def test_rejects_invalid_ipv6_bracket_host(self) -> None:
        with self.assertRaises(RepositoryIdentityError):
            normalize_remote_url("https://[invalid/org/repo")

    def test_rejects_scp_like_local_path_with_slash_in_host(self) -> None:
        with self.assertRaises(RepositoryIdentityError):
            normalize_remote_url("git@foo/bar:baz")

    def test_scp_local_path_not_equated_to_https_identity(self) -> None:
        https_identity = normalize_remote_url("https://foo/bar/baz")
        with self.assertRaises(RepositoryIdentityError):
            normalize_remote_url("git@foo/bar:baz")
        self.assertEqual(normalize_remote_url("git@foo:bar/baz"), "foo/bar/baz")
        self.assertEqual(https_identity, "foo/bar/baz")


if __name__ == "__main__":
    unittest.main()
