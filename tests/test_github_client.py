"""Tests for GitHub client compatibility and interface."""
from __future__ import annotations

import inspect

import pytest

from polaris_pr_intel.github.async_client import GitHubClientWrapper
from polaris_pr_intel.github.client import GitHubClient


class TestClientCompatibility:
    """Ensure GitHubClientWrapper maintains interface compatibility with GitHubClient."""

    def test_wrapper_has_all_public_methods(self):
        """Verify wrapper implements all public methods from the original client."""
        # Get all public methods from GitHubClient
        original_methods = {
            name: method
            for name, method in inspect.getmembers(GitHubClient, predicate=inspect.isfunction)
            if not name.startswith("_")
        }

        # Get all public methods from GitHubClientWrapper
        wrapper_methods = {
            name: method
            for name, method in inspect.getmembers(GitHubClientWrapper, predicate=inspect.isfunction)
            if not name.startswith("_")
        }

        # Verify wrapper has all original methods
        missing_methods = set(original_methods.keys()) - set(wrapper_methods.keys())
        assert not missing_methods, (
            f"GitHubClientWrapper is missing methods: {missing_methods}\n"
            f"Original methods: {sorted(original_methods.keys())}\n"
            f"Wrapper methods: {sorted(wrapper_methods.keys())}"
        )

    def test_wrapper_method_signatures_match(self):
        """Verify wrapper methods have the same signatures as original client."""
        original_methods = {
            name: method
            for name, method in inspect.getmembers(GitHubClient, predicate=inspect.isfunction)
            if not name.startswith("_")
        }

        wrapper_methods = {
            name: method
            for name, method in inspect.getmembers(GitHubClientWrapper, predicate=inspect.isfunction)
            if not name.startswith("_")
        }

        signature_mismatches = []
        for name in original_methods:
            if name not in wrapper_methods:
                continue  # Caught by test_wrapper_has_all_public_methods

            orig_sig = inspect.signature(original_methods[name])
            wrap_sig = inspect.signature(wrapper_methods[name])

            # Compare parameters (excluding 'self')
            orig_params = list(orig_sig.parameters.values())[1:]  # Skip 'self'
            wrap_params = list(wrap_sig.parameters.values())[1:]  # Skip 'self'

            if len(orig_params) != len(wrap_params):
                signature_mismatches.append(
                    f"{name}: parameter count mismatch - "
                    f"original has {len(orig_params)}, wrapper has {len(wrap_params)}"
                )
                continue

            for orig_p, wrap_p in zip(orig_params, wrap_params):
                if orig_p.name != wrap_p.name:
                    signature_mismatches.append(
                        f"{name}: parameter name mismatch - "
                        f"'{orig_p.name}' vs '{wrap_p.name}'"
                    )
                if orig_p.default != wrap_p.default:
                    signature_mismatches.append(
                        f"{name}: default value mismatch for '{orig_p.name}' - "
                        f"{orig_p.default} vs {wrap_p.default}"
                    )
                if orig_p.annotation != wrap_p.annotation:
                    signature_mismatches.append(
                        f"{name}: type annotation mismatch for '{orig_p.name}' - "
                        f"{orig_p.annotation} vs {wrap_p.annotation}"
                    )

        assert not signature_mismatches, (
            "Method signature mismatches found:\n" + "\n".join(f"  - {m}" for m in signature_mismatches)
        )

    def test_wrapper_initializes_correctly(self):
        """Verify wrapper can be instantiated with same parameters as original."""
        token = "test_token"
        owner = "test_owner"
        repo = "test_repo"

        # Both should accept the same constructor parameters
        original = GitHubClient(token, owner, repo)
        wrapper = GitHubClientWrapper(token, owner, repo)

        original.close()
        wrapper.close()

    def test_all_expected_methods_present(self):
        """Explicit test for all expected public methods."""
        expected_methods = [
            "close",
            "get_pull_request",
            "get_pull_request_activity_metrics",
            "get_pull_request_diff",
            "list_recent_pull_requests",
            "list_recent_issues",
        ]

        wrapper = GitHubClientWrapper("token", "owner", "repo")

        for method_name in expected_methods:
            assert hasattr(wrapper, method_name), f"Missing method: {method_name}"
            assert callable(getattr(wrapper, method_name)), f"Method not callable: {method_name}"
