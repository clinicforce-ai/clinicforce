#!/usr/bin/env python3
"""Run an AWS operation with bounded, continuous CloudFormation/ECS progress.

Only deployment metadata is printed. Application logs can contain patient data.
Keep this file identical in clinicforce and clinicforce-aws.
"""
import argparse
import json
import subprocess
import time


def aws(*args):
    result = subprocess.run(["aws", *args, "--output", "json", "--no-cli-pager"],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("AWS progress query failed; inspect the deployment command output")
    return json.loads(result.stdout)


def progress(stack, seen):
    events = aws("cloudformation", "describe-stack-events", "--stack-name", stack,
                 "--max-items", "30")["StackEvents"]
    for event in reversed(events):
        if event["EventId"] not in seen:
            seen.add(event["EventId"])
            print(event["Timestamp"], event["LogicalResourceId"], event["ResourceStatus"],
                  event.get("ResourceStatusReason", ""), flush=True)
    data = aws("ecs", "describe-services", "--cluster", stack, "--services", "backend", "hapi")
    for service in data.get("services", []):
        print(f"ECS {service['serviceName']}: desired={service['desiredCount']} "
              f"running={service['runningCount']} pending={service['pendingCount']} "
              f"rollout={[d.get('rolloutState') for d in service.get('deployments', [])]}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", required=True, choices=["clinicforce-staging", "clinicforce-production"])
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide an operation after --")
    started, seen = time.monotonic(), set()
    process = subprocess.Popen(command)
    try:
        while True:
            print(f"Deployment progress: {args.stack}, elapsed={int(time.monotonic()-started)}s", flush=True)
            try:
                progress(args.stack, seen)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                print(f"Progress temporarily unavailable: {exc}", flush=True)
            result = process.poll()
            if result is not None:
                return result
            if time.monotonic() - started > args.timeout:
                print("Monitor deadline reached; AWS operation may still be running. Inspect before retrying.", flush=True)
                return 124
            time.sleep(15)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
