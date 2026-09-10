"""Run with PYTHONPATH=backend backend/venv/bin/python -m app.insurance.check_availity.

Prints status codes only; never tokens, credentials, patient data or raw responses.
Demo inquiries always use Availity's synthetic inputs. Production checks metadata only.
"""
import argparse
import asyncio
import json

from app.insurance.availity import Settings, gateway, PATHS, DEMO_SCENARIOS, CoverageInquiry


async def check(payer_id: str | None) -> int:
    try:
        settings = Settings.read()
    except ValueError as exc:
        print(str(exc))
        return 1
    print(json.dumps({"environment": settings.environment, "gateway": settings.base_url,
                      "credentials_configured": bool(settings.client_id and settings.client_secret)}))
    if not settings.client_id or not settings.client_secret:
        return 1
    client = gateway(settings)
    failed = False
    def report(stage, response):
        nonlocal failed
        print(json.dumps({"stage": stage, **{k: response.get(k) for k in ("http_status", "outcome", "mock_response")}}))
        failed |= response["outcome"] not in {"complete", "pending"}
    if settings.environment == "demo":
        inquiry = CoverageInquiry('BCBSF', '1234567893', 'G2509', 'H39522358', 'GEORGE', 'KOVAL', '1999-01-01')
        for resource in ("coverage", "estimate", "authorization"):
            response = await client.request("POST", PATHS[resource], data=inquiry.as_form() if resource == "coverage" else None,
                                            body={} if resource != "coverage" else None, resource=resource, scenario=DEMO_SCENARIOS[resource][0])
            report(resource + " submit", response)
            if response["outcome"] == "pending" and response.get("resource_id"):
                report(resource + " retrieve", await client.request("GET", PATHS[resource] + "/" + response["resource_id"],
                                                                     resource=resource, scenario=DEMO_SCENARIOS[resource][1]))
        payer_id = payer_id or "BCBSF"
    if payer_id:
        report("payer directory", await client.request("GET", "/v1/availity-payer-list", params={"payerId": payer_id, "submissionMode": "API"}))
        for kind, subtype in (("270", None), ("professional-claims", "PRE_DETERMINATION"), ("service-reviews", "HS"), ("service-reviews", "SC")):
            params = {"type": kind, "payerId": payer_id}
            if subtype:
                params["subtypeId"] = subtype
            report("configuration " + kind + (" " + subtype if subtype else ""), await client.request("GET", "/v1/configurations", params=params))
    elif settings.environment == "production":
        print("No live patient inquiry sent. Use --payer-id with an Availity payer identifier to check metadata access.")
    return int(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payer-id", help="Availity payer ID for metadata checks; never a patient/member ID")
    raise SystemExit(asyncio.run(check(parser.parse_args().payer_id)))
