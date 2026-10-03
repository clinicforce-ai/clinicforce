#!/usr/bin/env python3
"""Check public release routes and the synthetic staging staff session flow."""
import argparse
import http.cookiejar
import json
import os
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["staging", "production"])
    args = parser.parse_args()
    base = "https://staging.clinicforce.ai" if args.environment == "staging" else "https://app.clinicforce.ai"
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, method="GET", body=None, expected=200):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Origin": base, "Content-Type": "application/json"})
        try:
            with client.open(req, timeout=30) as response:
                if response.status != expected:
                    raise RuntimeError(f"{path}: unexpected HTTP status")
                data = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{path}: HTTP {exc.code}; response body withheld") from None
        print(f"PASS {method} {path} HTTP {expected}", flush=True)
        return data

    for path in ["/", "/healthz", "/readyz", "/api/docs", "/api/redoc", "/api/openapi.json"]:
        data = request(path)
        if path in {"/api/docs", "/api/redoc"} and b"Staging and production releases" not in data:
            raise RuntimeError("Expected release documentation is absent")
        if path == "/api/openapi.json" and "paths" not in json.loads(data):
            raise RuntimeError("Invalid API reference")
    if args.environment == "staging":
        password = os.environ.get("STAGING_SMOKE_PASSWORD")
        if not password:
            raise RuntimeError("Configure staging-only STAGING_SMOKE_PASSWORD; authentication checks cannot be skipped")
        try:
            request("/api/staff/login", "POST", {"username": "release-qa@clinicforce.invalid", "password": password})
            staff = json.loads(request("/api/staff/session"))["staff"]
            if staff["practice_id"] != "release-qa":
                raise RuntimeError("Smoke account must belong to the isolated release-qa practice")
        finally:
            request("/api/staff/logout", "POST", expected=204)
        try:
            request("/api/staff/session")
        except RuntimeError as exc:
            if "HTTP 401" not in str(exc):
                raise
        else:
            raise RuntimeError("Logout did not revoke the session")
        print("PASS synthetic staff sign-in, session and logout; provider workflows require separate verification")


if __name__ == "__main__":
    main()
