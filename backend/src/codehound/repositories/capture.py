"""Capture public PR metadata and a pinned diff without executing its code."""

import argparse
import asyncio
import json
from pathlib import Path

from codehound.repositories.github_client import GitHubClient, GitHubFailure
from codehound.repositories.intake import collect_snapshot
from codehound.repositories.urls import parse_pull_url


async def capture(pr_url):
    reference = parse_pull_url(pr_url)
    async with GitHubClient(None) as provider:
        async with asyncio.timeout(90):
            return await collect_snapshot(reference, provider)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr_url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(capture(args.pr_url))
    except (GitHubFailure, ValueError, TimeoutError) as exc:
        parser.exit(1, f"Intake failed: {exc}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as file:
        json.dump(result, file, indent=2)
        file.write("\n")
    print(f"Captured {result['summary']['files_changed']} changed files to {args.output}")
    print("No code or tests were executed.")


if __name__ == "__main__":
    main()
