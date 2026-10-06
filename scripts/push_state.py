"""Retry state delivery without rerunning scans or Telegram notifications."""
import os
import subprocess
import time


def run(*args):
    return subprocess.run(["git", *args], check=False).returncode


def main():
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    for attempt in range(4):
        if run("push", "origin", f"HEAD:refs/heads/{branch}") == 0:
            print("State successfully saved to GitHub")
            return
        if attempt == 3:
            break
        print(f"Push failed; retry {attempt + 1}/3 after checking the remote branch", flush=True)
        time.sleep(5 * (attempt + 1))
        if run("fetch", "origin", branch) != 0:
            continue
        if run("rebase", "FETCH_HEAD") != 0:
            run("rebase", "--abort")
            raise RuntimeError("Remote changes conflict with saved state. Stopped without overwriting them.")
    raise RuntimeError("GitHub rejected state delivery after four attempts. See git errors above.")

if __name__ == "__main__":
    main()
