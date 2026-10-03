#!/usr/bin/env python3
"""Reject promotion unless the source is a successful staging run on main."""
import json
import os
import re
import subprocess
import sys


def validate_run(run, jobs):
    if (run.get("path") != ".github/workflows/deploy.yml"
            or run.get("head_branch") != "main"
            or run.get("event") not in {"push", "workflow_dispatch"}
            or run.get("conclusion") != "success"
            or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))
            or not any(job["name"] == "Deploy staging" and job["conclusion"] == "success" for job in jobs)):
        raise ValueError("Promotion requires a successful main-branch staging deployment, including smoke tests")
    return run["head_sha"]


def main():
    run_id = sys.argv[1]
    if not run_id.isdigit():
        raise ValueError("staging_run_id must be a numeric Actions run ID")
    repo = os.environ["GITHUB_REPOSITORY"]
    def get(path):
        return json.loads(subprocess.check_output(["gh", "api", f"repos/{repo}/{path}"]))
    run = get(f"actions/runs/{run_id}")
    sha = validate_run(run, get(f"actions/runs/{run_id}/jobs?per_page=100")["jobs"])
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"sha={sha}\n")
    print(f"Validated staging run {run_id}, commit {sha}")


if __name__ == "__main__":
    main()
