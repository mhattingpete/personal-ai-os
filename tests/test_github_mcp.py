"""Tests for the GitHub MCP server."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pai_mcp.github import (
    _run_gh,
    _get_current_user,
    list_my_prs,
    list_prs_with_reviews,
    get_pr_reviews,
    get_pr_diff,
    format_review_for_claude,
)


class TestGhCLIWrapper:
    """Tests for gh CLI wrapper functions."""

    @pytest.mark.asyncio
    async def test_run_gh_success(self):
        """Test successful gh command execution."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (
                b'[{"number": 1, "title": "Test PR"}]',
                b"",
            )
            mock_exec.return_value = mock_proc

            result = await _run_gh("pr", "list", "--json", "number,title")

            assert result == [{"number": 1, "title": "Test PR"}]

    @pytest.mark.asyncio
    async def test_run_gh_error(self):
        """Test gh command error handling."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate.return_value = (b"", b"Error: not authenticated")
            mock_exec.return_value = mock_proc

            result = await _run_gh("pr", "list")

            assert "error" in result
            assert "not authenticated" in result["error"]

    @pytest.mark.asyncio
    async def test_run_gh_non_json_output(self):
        """Test handling of non-JSON output (like diffs)."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"diff --git a/file.py b/file.py", b"")
            mock_exec.return_value = mock_proc

            result = await _run_gh("pr", "diff", "1")

            assert "raw" in result
            assert "diff --git" in result["raw"]

    def test_get_current_user_success(self):
        """Test getting current authenticated user."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="testuser\n")

            result = _get_current_user()

            assert result == "testuser"

    def test_get_current_user_not_authenticated(self):
        """Test handling when not authenticated."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")

            result = _get_current_user()

            assert result is None


class TestListMyPRs:
    """Tests for list_my_prs tool."""

    @pytest.mark.asyncio
    async def test_list_my_prs_success(self):
        """Test listing PRs authored by user."""
        with patch("pai_mcp.github._get_current_user", return_value="testuser"), \
             patch("pai_mcp.github._run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = [
                {"number": 1, "title": "Fix bug", "url": "https://github.com/org/repo/pull/1"},
                {"number": 2, "title": "Add feature", "url": "https://github.com/org/repo/pull/2"},
            ]

            result = await list_my_prs("open")

            assert result["user"] == "testuser"
            assert result["state"] == "open"
            assert len(result["prs"]) == 2

    @pytest.mark.asyncio
    async def test_list_my_prs_not_authenticated(self):
        """Test error when not authenticated."""
        with patch("pai_mcp.github._get_current_user", return_value=None):
            result = await list_my_prs()

            assert "error" in result
            assert "Not authenticated" in result["error"]

    @pytest.mark.asyncio
    async def test_list_my_prs_empty(self):
        """Test when user has no PRs."""
        with patch("pai_mcp.github._get_current_user", return_value="testuser"), \
             patch("pai_mcp.github._run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = []

            result = await list_my_prs()

            assert result["prs"] == []


class TestListPRsWithReviews:
    """Tests for list_prs_with_reviews tool."""

    @pytest.mark.asyncio
    async def test_list_prs_with_reviews(self):
        """Test listing PRs that have reviews."""
        with patch("pai_mcp.github._get_current_user", return_value="testuser"), \
             patch("pai_mcp.github._run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = [
                {
                    "number": 1,
                    "title": "PR with review",
                    "url": "https://github.com/org/repo/pull/1",
                    "headRepository": {"nameWithOwner": "org/repo"},
                    "headRefName": "feature-branch",
                    "reviews": [{"state": "CHANGES_REQUESTED"}],
                },
            ]

            result = await list_prs_with_reviews(24)

            assert result["user"] == "testuser"
            assert len(result["prs_with_reviews"]) == 1
            assert result["prs_with_reviews"][0]["repo"] == "org/repo"
            assert result["prs_with_reviews"][0]["review_count"] == 1

    @pytest.mark.asyncio
    async def test_list_prs_with_reviews_filters_no_reviews(self):
        """Test that PRs without reviews are filtered out."""
        with patch("pai_mcp.github._get_current_user", return_value="testuser"), \
             patch("pai_mcp.github._run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = [
                {
                    "number": 1,
                    "title": "PR without review",
                    "url": "https://github.com/org/repo/pull/1",
                    "headRepository": {"nameWithOwner": "org/repo"},
                    "headRefName": "branch",
                    "reviews": [],  # No reviews
                },
            ]

            result = await list_prs_with_reviews()

            assert result["prs_with_reviews"] == []


class TestGetPRReviews:
    """Tests for get_pr_reviews tool."""

    @pytest.mark.asyncio
    async def test_get_pr_reviews_success(self):
        """Test getting reviews for a PR."""
        with patch("pai_mcp.github._run_gh", new_callable=AsyncMock) as mock_gh:
            # First call: PR details
            # Second call: reviews
            # Third call: comments
            mock_gh.side_effect = [
                {"number": 1, "title": "Test PR", "author": {"login": "testuser"}},
                [{"id": 123, "user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED", "body": "Fix this"}],
                [{"id": 456, "user": {"login": "reviewer"}, "path": "file.py", "line": 10, "body": "Typo here"}],
            ]

            result = await get_pr_reviews("org/repo", 1)

            assert result["repo"] == "org/repo"
            assert result["pr"]["title"] == "Test PR"
            assert len(result["reviews"]) == 1
            assert result["reviews"][0]["state"] == "CHANGES_REQUESTED"
            assert len(result["comments"]) == 1
            assert result["comments"][0]["path"] == "file.py"

    @pytest.mark.asyncio
    async def test_get_pr_reviews_pr_not_found(self):
        """Test error when PR not found."""
        with patch("pai_mcp.github._run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = {"error": "Could not resolve PR", "returncode": 1}

            result = await get_pr_reviews("org/repo", 999)

            assert "error" in result


class TestGetPRDiff:
    """Tests for get_pr_diff tool."""

    @pytest.mark.asyncio
    async def test_get_pr_diff_success(self):
        """Test getting diff for a PR."""
        with patch("pai_mcp.github._run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = [
                {"raw": "diff --git a/file.py b/file.py\n+new line"},
                {"files": [{"path": "file.py", "additions": 1, "deletions": 0}]},
            ]

            result = await get_pr_diff("org/repo", 1)

            assert result["repo"] == "org/repo"
            assert result["pr_number"] == 1
            assert "diff --git" in result["diff"]
            assert len(result["files"]) == 1


class TestFormatReviewForClaude:
    """Tests for format_review_for_claude tool."""

    @pytest.mark.asyncio
    async def test_format_review_for_claude(self):
        """Test formatting PR review for Claude Code."""
        with patch("pai_mcp.github.get_pr_reviews", new_callable=AsyncMock) as mock_reviews, \
             patch("pai_mcp.github.get_pr_diff", new_callable=AsyncMock) as mock_diff:
            mock_reviews.return_value = {
                "repo": "org/repo",
                "pr": {
                    "number": 1,
                    "title": "Fix authentication",
                    "headRefName": "fix-auth",
                    "baseRefName": "main",
                    "additions": 50,
                    "deletions": 10,
                    "changedFiles": 3,
                    "files": [{"path": "auth.py"}],
                },
                "reviews": [
                    {"author": "reviewer", "state": "CHANGES_REQUESTED", "body": "Please add tests"},
                ],
                "comments": [
                    {"author": "reviewer", "path": "auth.py", "line": 42, "body": "This could be simplified"},
                ],
            }
            mock_diff.return_value = {"diff": "...", "files": []}

            result = await format_review_for_claude("org/repo", 1)

            assert result["repo"] == "org/repo"
            assert result["pr_number"] == 1
            assert result["branch"] == "fix-auth"
            assert "prompt" in result
            assert "Fix authentication" in result["prompt"]
            assert "@reviewer" in result["prompt"]
            assert "Please add tests" in result["prompt"]
            assert "auth.py" in result["prompt"]
            assert "Line 42" in result["prompt"]

    @pytest.mark.asyncio
    async def test_format_review_handles_error(self):
        """Test error handling when fetching review fails."""
        with patch("pai_mcp.github.get_pr_reviews", new_callable=AsyncMock) as mock_reviews:
            mock_reviews.return_value = {"error": "PR not found"}

            result = await format_review_for_claude("org/repo", 999)

            assert "error" in result
