import unittest

import httpx

from app.insurance.availity import AvailityClient, CoverageInquiry


INQUIRY = CoverageInquiry(
    payer_id="BCBSF",
    provider_npi="1234567893",
    submitter_id="G2509",
    member_id="H39522358",
    patient_first_name="GEORGE",
    patient_last_name="KOVAL",
    patient_birth_date="1999-01-01",
)


class AvailityClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_demo_outcomes_are_normalized(self) -> None:
        scenarios = {
            "complete": (200, _coverage("Complete", "4"), "complete"),
            "in-progress": (202, _coverage("In Progress", "0"), "pending"),
            "retrying": (200, _coverage("Communication Error, Retrying", "R1"), "retrying"),
            "payer-error": (
                200,
                _coverage("Request Error", "19", [{"message": "Provider is ineligible."}]),
                "payer_error",
            ),
            "input-error": (400, {"errors": [{"field": "providerNpi"}]}, "input_error"),
        }

        for scenario, (status, payload, expected) in scenarios.items():
            with self.subTest(scenario=scenario):
                client = AvailityClient("demo-id", "demo-secret", transport=_transport(status, payload))
                result = await client.check_coverage(INQUIRY, mock_scenario=scenario)
                self.assertEqual(result["outcome"], expected)


def _coverage(status: str, status_code: str, validation_messages: list[dict[str, str]] | None = None) -> dict:
    return {
        "coverages": [
            {
                "id": "123",
                "status": status,
                "statusCode": status_code,
                "validationMessages": validation_messages or [],
                "payer": {"payerId": "123"},
            }
        ]
    }


def _transport(status: int, payload: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"access_token": "demo-token"})
        assert request.url.path == "/availity/v1/coverages"
        assert request.headers["authorization"] == "Bearer demo-token"
        return httpx.Response(
            status, json=payload,
            headers={"x-api-id": "transaction-123", "x-api-mock-response": "true"},
        )

    return httpx.MockTransport(handler)
