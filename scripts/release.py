#!/usr/bin/env python3
"""Deploy a built release; never rebuild or read integration parameter values."""
import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from watch_deploy import aws


def command(*args):
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["staging", "production"])
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    stack = f"clinicforce-{args.environment}"
    manifest = json.loads(Path("release/manifest.json").read_text())
    if manifest["sha"] != args.sha:
        raise ValueError("Release artifact SHA does not match the verified source run")
    for service in ["backend", "hapi"]:
        pattern = rf"311057178360\.dkr\.ecr\.us-east-1\.amazonaws\.com/clinicforce-staging-{service}@sha256:[0-9a-f]{{64}}"
        if not re.fullmatch(pattern, manifest[service]):
            raise ValueError("Release must use a digest from the staging image repository")
    if not Path("release/frontend/index.html").is_file():
        raise ValueError("Release frontend is absent")
    def watch(*args):
        command(sys.executable, "scripts/watch_deploy.py", "--stack", stack, "--", *args)
    # A second repository may be updating this stack. Wait visibly, then let
    # CloudFormation reject a competing update; never retry an uncertain write.
    status = aws("cloudformation", "describe-stacks", "--stack-name", stack)["Stacks"][0]["StackStatus"]
    if status.endswith("_IN_PROGRESS"):
        watch("aws", "cloudformation", "wait", "stack-update-complete", "--stack-name", stack)
    keys = aws("cloudformation", "describe-stacks", "--stack-name", stack,
               "--query", "Stacks[0].Parameters[].ParameterKey")
    if "EnvironmentName" not in keys:
        raise ValueError("Apply the staging-aware infrastructure template with the operator identity before an application release")
    if not {"BackendImageUri", "HapiImageUri"}.issubset(keys):
        raise ValueError("Target stack lacks application image parameters")
    images = {"BackendImageUri": manifest["backend"], "HapiImageUri": manifest["hapi"]}
    parameters = [{"ParameterKey": key, **({"ParameterValue": images[key]} if key in images else {"UsePreviousValue": True})}
                  for key in keys]
    result = subprocess.run(["aws", "cloudformation", "update-stack", "--stack-name", stack,
                             "--use-previous-template", "--capabilities", "CAPABILITY_NAMED_IAM",
                             "--parameters", json.dumps(parameters)], capture_output=True, text=True)
    if result.returncode and "No updates are to be performed" not in result.stderr:
        raise RuntimeError(result.stderr)
    if result.returncode == 0:
        watch("aws", "cloudformation", "wait", "stack-update-complete", "--stack-name", stack)
    watch("aws", "ecs", "wait", "services-stable", "--cluster", stack, "--services", "backend", "hapi")
    for service in ["backend", "hapi"]:
        deployed = aws("ecs", "describe-services", "--cluster", stack, "--services", service)["services"][0]
        definition = aws("ecs", "describe-task-definition", "--task-definition", deployed["taskDefinition"])["taskDefinition"]
        image = next(c["image"] for c in definition["containerDefinitions"] if c["name"] == service)
        if image != manifest[service]:
            raise RuntimeError("ECS rolled back or did not adopt the selected release")
    outputs = {v["OutputKey"]: v["OutputValue"] for v in aws("cloudformation", "describe-stacks", "--stack-name", stack)["Stacks"][0]["Outputs"]}
    bucket = outputs["StaticBucketName"]
    command("aws", "s3", "sync", "release/frontend/", f"s3://{bucket}/", "--exclude", "index.html",
            "--cache-control", "public,max-age=31536000,immutable")
    command("aws", "s3", "cp", "release/frontend/index.html", f"s3://{bucket}/index.html",
            "--content-type", "text/html", "--cache-control", "no-store,max-age=0,must-revalidate")
    invalidation = aws("cloudfront", "create-invalidation", "--distribution-id", outputs["DistributionId"], "--paths", "/*")
    watch("aws", "cloudfront", "wait", "invalidation-completed", "--distribution-id", outputs["DistributionId"],
          "--id", invalidation["Invalidation"]["Id"])
    with urllib.request.urlopen(outputs["AppUrl"] + "/index.html", timeout=30) as response:
        if response.read() != Path("release/frontend/index.html").read_bytes():
            raise RuntimeError("Public frontend does not match the staged release artifact")
    command(sys.executable, "scripts/smoke_release.py", args.environment)
    print(f"Released {args.sha} to {args.environment}")


if __name__ == "__main__":
    main()
