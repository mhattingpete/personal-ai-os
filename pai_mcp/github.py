"""GitHub MCP server for PAI.

Provides tools to interact with GitHub PRs and reviews using the gh CLI.
Run with: uv run pai-github-mcp
"""

import asyncio
import json
import subprocess

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("PAI GitHub", json_response=True)


async def _run_gh(*args: str) -> dict | list | None:
    """Run gh CLI command and parse JSON output."""
    cmd = ["gh", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() if stderr else "Unknown error"
            return {"error": error_msg, "returncode": proc.returncode}

        if not stdout:
            return None

        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        # Return raw output if not JSON
        return {"raw": stdout.decode().strip()}
    except FileNotFoundError:
        return {"error": "gh CLI not found. Install from https://cli.github.com"}


def _get_current_user() -> str | None:
    """Get the authenticated GitHub username synchronously."""
    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


@mcp.tool()
async def list_my_prs(state: str = "open") -> dict:
    """List PRs authored by the authenticated user.

    Args:
        state: PR state filter - "open", "closed", "merged", or "all"

    Returns:
        Dict with list of PRs
    """
    user = _get_current_user()
    if not user:
        return {"error": "Not authenticated with gh CLI"}

    result = await _run_gh(
        "pr", "list",
        "--author", user,
        "--state", state,
        "--json", "number,title,url,state,headRepository,createdAt,updatedAt,reviewDecision,reviews",
        "--limit", "50",
    )

    if isinstance(result, dict) and "error" in result:
        return result

    return {
        "user": user,
        "state": state,
        "prs": result or [],
    }


@mcp.tool()
async def list_prs_with_reviews(since_hours: int = 24) -> dict:
    """List PRs authored by me that have received reviews recently.

    Args:
        since_hours: Look for reviews in the last N hours

    Returns:
        Dict with PRs that have new reviews
    """
    user = _get_current_user()
    if not user:
        return {"error": "Not authenticated with gh CLI"}

    # Get all open PRs by the user
    prs_result = await _run_gh(
        "pr", "list",
        "--author", user,
        "--state", "open",
        "--json", "number,title,url,headRepository,reviews,headRefName",
        "--limit", "50",
    )

    if isinstance(prs_result, dict) and "error" in prs_result:
        return prs_result

    prs_with_reviews = []
    for pr in prs_result or []:
        reviews = pr.get("reviews", [])
        if reviews:
            prs_with_reviews.append({
                "number": pr["number"],
                "title": pr["title"],
                "url": pr["url"],
                "repo": pr["headRepository"]["nameWithOwner"],
                "branch": pr["headRefName"],
                "review_count": len(reviews),
                "latest_review": reviews[-1] if reviews else None,
            })

    return {
        "user": user,
        "prs_with_reviews": prs_with_reviews,
    }


@mcp.tool()
async def get_pr_reviews(repo: str, pr_number: int) -> dict:
    """Get all reviews and comments for a PR.

    Args:
        repo: Repository in owner/repo format
        pr_number: Pull request number

    Returns:
        Dict with reviews, comments, and review threads
    """
    # Get PR details
    pr_result = await _run_gh(
        "pr", "view", str(pr_number),
        "--repo", repo,
        "--json", "number,title,body,author,state,additions,deletions,changedFiles,files,headRefName,baseRefName",
    )

    if isinstance(pr_result, dict) and "error" in pr_result:
        return pr_result

    # Get reviews
    reviews_result = await _run_gh(
        "api", f"repos/{repo}/pulls/{pr_number}/reviews",
    )

    # Get review comments (inline comments)
    comments_result = await _run_gh(
        "api", f"repos/{repo}/pulls/{pr_number}/comments",
    )

    reviews = []
    if isinstance(reviews_result, list):
        for review in reviews_result:
            reviews.append({
                "id": review.get("id"),
                "author": review.get("user", {}).get("login"),
                "state": review.get("state"),  # APPROVED, CHANGES_REQUESTED, COMMENTED
                "body": review.get("body"),
                "submitted_at": review.get("submitted_at"),
            })

    comments = []
    if isinstance(comments_result, list):
        for comment in comments_result:
            comments.append({
                "id": comment.get("id"),
                "author": comment.get("user", {}).get("login"),
                "path": comment.get("path"),
                "line": comment.get("line") or comment.get("original_line"),
                "body": comment.get("body"),
                "created_at": comment.get("created_at"),
                "diff_hunk": comment.get("diff_hunk"),
                "in_reply_to_id": comment.get("in_reply_to_id"),
            })

    return {
        "repo": repo,
        "pr": pr_result,
        "reviews": reviews,
        "comments": comments,
    }


@mcp.tool()
async def get_pr_diff(repo: str, pr_number: int) -> dict:
    """Get the diff for a PR.

    Args:
        repo: Repository in owner/repo format
        pr_number: Pull request number

    Returns:
        Dict with diff content and changed files
    """
    # Get diff
    diff_result = await _run_gh(
        "pr", "diff", str(pr_number),
        "--repo", repo,
    )

    # Get file list
    files_result = await _run_gh(
        "pr", "view", str(pr_number),
        "--repo", repo,
        "--json", "files",
    )

    diff_text = ""
    if isinstance(diff_result, dict):
        if "raw" in diff_result:
            diff_text = diff_result["raw"]
        elif "error" in diff_result:
            return diff_result

    files = []
    if isinstance(files_result, dict) and "files" in files_result:
        files = files_result["files"]

    return {
        "repo": repo,
        "pr_number": pr_number,
        "diff": diff_text,
        "files": files,
    }


@mcp.tool()
async def format_review_for_claude(repo: str, pr_number: int) -> dict:
    """Format PR review data as a prompt for Claude Code.

    Args:
        repo: Repository in owner/repo format
        pr_number: Pull request number

    Returns:
        Dict with formatted prompt and metadata
    """
    # Fetch all PR data
    reviews_data = await get_pr_reviews(repo, pr_number)
    if "error" in reviews_data:
        return reviews_data

    diff_data = await get_pr_diff(repo, pr_number)

    pr = reviews_data.get("pr", {})
    reviews = reviews_data.get("reviews", [])
    comments = reviews_data.get("comments", [])

    # Build the prompt
    prompt_parts = [
        f"# PR Review Implementation Task",
        f"",
        f"## Pull Request: {pr.get('title', 'Unknown')} (#{pr_number})",
        f"**Repository:** {repo}",
        f"**Branch:** {pr.get('headRefName', 'unknown')} -> {pr.get('baseRefName', 'main')}",
        f"**Changes:** +{pr.get('additions', 0)} -{pr.get('deletions', 0)} in {pr.get('changedFiles', 0)} files",
        f"",
    ]

    # Add reviews
    if reviews:
        prompt_parts.append("## Reviews")
        for review in reviews:
            state_emoji = {
                "APPROVED": "approved",
                "CHANGES_REQUESTED": "changes requested",
                "COMMENTED": "commented",
            }.get(review.get("state", ""), review.get("state", ""))
            prompt_parts.append(f"")
            prompt_parts.append(f"### @{review.get('author', 'unknown')} ({state_emoji})")
            if review.get("body"):
                prompt_parts.append(f"{review['body']}")

    # Add inline comments grouped by file
    if comments:
        prompt_parts.append("")
        prompt_parts.append("## Inline Comments")

        # Group by file
        comments_by_file: dict[str, list] = {}
        for comment in comments:
            path = comment.get("path", "unknown")
            if path not in comments_by_file:
                comments_by_file[path] = []
            comments_by_file[path].append(comment)

        for path, file_comments in comments_by_file.items():
            prompt_parts.append(f"")
            prompt_parts.append(f"### {path}")
            for comment in file_comments:
                line = comment.get("line", "?")
                author = comment.get("author", "unknown")
                body = comment.get("body", "")
                prompt_parts.append(f"")
                prompt_parts.append(f"**Line {line}** (@{author}):")
                prompt_parts.append(f"{body}")

    # Add instructions
    prompt_parts.extend([
        "",
        "## Your Task",
        "",
        "1. Read and understand each review comment above",
        "2. Implement the requested changes",
        "3. Commit your changes with a descriptive message",
        "4. Push to update the PR",
        "",
        "Focus on addressing each comment. If a suggestion is unclear or you disagree, explain why.",
    ])

    prompt = "\n".join(prompt_parts)

    return {
        "repo": repo,
        "pr_number": pr_number,
        "branch": pr.get("headRefName"),
        "prompt": prompt,
        "review_count": len(reviews),
        "comment_count": len(comments),
        "files_changed": [f.get("path") for f in pr.get("files", [])],
    }


def main():
    """Run the GitHub MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
