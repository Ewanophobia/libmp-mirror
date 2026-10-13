"""Keep the public repository's release schedule active during quiet periods."""

from __future__ import annotations

import subprocess
import time


KEEPALIVE_SECONDS = 45 * 24 * 60 * 60


def keepalive_due(last_commit_timestamp: int, now_timestamp: float) -> bool:
    """Leave a margin before GitHub's 60-day public-repository inactivity limit."""
    return now_timestamp - last_commit_timestamp >= KEEPALIVE_SECONDS


def main() -> None:
    last_commit_timestamp = int(
        subprocess.check_output(["git", "log", "-1", "--format=%ct"], text=True).strip()
    )
    if not keepalive_due(last_commit_timestamp, time.time()):
        print("No schedule keepalive is needed.")
        return
    # Do not accidentally include a caller's outstanding changes in this commit.
    subprocess.run(["git", "diff", "--exit-code", "HEAD"], check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "chore: keep upstream release checks active"],
        check=True,
    )


if __name__ == "__main__":
    main()
