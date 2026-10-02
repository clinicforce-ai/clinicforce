import unittest

from app.insurance.commercial import CommercialError, estimate, normalize_stedi_271, parse_rate_csv
from app.insurance.commercial_store import select_matching_rates
from app.insurance.ecw_backend import _patient_matches
from app.api.routers.commercial_estimator import stedi_request_body


class CommercialRulesTest(unittest.TestCase):
    def test_rate_csv_and_cumulative_estimate(self):
        rows = parse_rate_csv(b"CPT,Allowed Amount,Network,POS,Effective From,Cash Price\n99204,250.00,PPO,11,2026-01-01,300.00\n", "rates.csv")
        self.assertEqual(rows[0]["allowed_cents"], 25000)
        result = estimate([{"code": "99204", "units": 1, "allowed_cents": 25000}], {"active": True, "coverage_level": "individual", "network_status": "in_network", "deductible_remaining_cents": 10000, "oop_remaining_cents": 20000, "copay_cents": 2500, "coinsurance_percent": 20, "has_secondary": False, "requires_review": False}, {"cost_share": "cumulative"})
        self.assertEqual(result["patient_cents"], 15500)
        self.assertEqual(result["plan_cents"], 9500)

    def test_unknown_benefit_never_becomes_zero(self):
        with self.assertRaisesRegex(CommercialError, "deductible"):
            estimate([{"code": "99204", "units": 1, "allowed_cents": 25000}], {"active": True, "coverage_level": "individual", "network_status": "in_network", "oop_remaining_cents": 20000, "has_secondary": False, "requires_review": False}, {"cost_share": "copay"})

    def test_rate_headers_are_required(self):
        with self.assertRaises(CommercialError):
            parse_rate_csv(b"CPT,Allowed Amount\n99204,250\n", "rates.csv")

    def test_stedi_remaining_individual_benefits_normalize(self):
        payload = {"plans": [{"benefits": {"statuses": [{"status": "Active"}]} }], "benefitsInformation": [
            {"code": "C", "coverageLevel": "Individual", "serviceTypeCodes": ["98"], "timeQualifierCode": "29", "benefitAmount": "500", "inPlanNetworkIndicatorCode": "Y"},
            {"code": "G", "coverageLevel": "Individual", "serviceTypeCodes": ["98"], "timeQualifierCode": "29", "benefitAmount": "2500", "inPlanNetworkIndicatorCode": "Y"},
            {"code": "B", "coverageLevel": "Individual", "serviceTypeCodes": ["98"], "benefitAmount": "40", "inPlanNetworkIndicatorCode": "Y", "authOrCertIndicator": "N"},
        ]}
        benefits = normalize_stedi_271(payload, "98", True)
        self.assertEqual(benefits["deductible_remaining_cents"], 50000)
        self.assertEqual(benefits["oop_remaining_cents"], 250000)
        self.assertEqual(benefits["copay_cents"], 4000)
        self.assertFalse(benefits["requires_review"])

    def test_stedi_family_or_missing_accumulator_requires_review(self):
        benefits = normalize_stedi_271({"benefitsInformation": [{"code": "1"}]}, "98", True)
        self.assertTrue(benefits["requires_review"])

    def test_missing_or_required_authorization_blocks_price(self):
        base = {"plans": [{"benefits": {"statuses": [{"status": "Active"}]}}], "benefitsInformation": [
            {"code": code, "coverageLevel": "Individual", "serviceTypeCodes": ["98"],
             "inPlanNetworkIndicatorCode": "Y", "timeQualifierCode": "29", "benefitAmount": amount}
            for code, amount in (("C", "0"), ("G", "1000"), ("B", "25"))]}
        self.assertEqual(normalize_stedi_271(base, "98", True)["authorization_signal"], "unknown")
        self.assertTrue(normalize_stedi_271(base, "98", True)["requires_review"])
        base["benefitsInformation"][2]["authOrCertIndicator"] = "Y"
        self.assertTrue(normalize_stedi_271(base, "98", True)["requires_review"])

    def test_contract_rate_requires_correct_provider_and_plan(self):
        network = {"rendering_npi": "1234567890", "tin": "123456789", "plan_matcher": "PPO"}
        profile = {"plan_matcher": "PPO"}
        rows = [{"id": "wrong-provider", "code": "99204", "allowed_cents": 100, "provider_npi": "0000000000"},
                {"id": "wrong-plan", "code": "99204", "allowed_cents": 200, "plan_matcher": "HMO"},
                {"id": "correct", "code": "99204", "allowed_cents": 25000, "provider_npi": "1234567890", "tin": "123456789", "plan_matcher": "PPO"}]
        self.assertEqual(select_matching_rates(rows, profile, network, [{"code": "99204", "units": 1}])[0]["id"], "correct")

    def test_cardless_patient_identity_and_dependent_request(self):
        patient = {"name": [{"use": "official", "given": ["Ada", "B"], "family": "Lovelace"}], "birthDate": "1980-01-01"}
        profile = {"patient_first_name": "Ada", "patient_middle_initial": "B", "patient_last_name": "Lovelace", "patient_birth_date": "1980-01-01"}
        self.assertTrue(_patient_matches(patient, profile))
        self.assertFalse(_patient_matches(patient, {**profile, "patient_middle_initial": "C"}))
        coverage = {"member_id": "M123", "relationship": "19", "subscriber_first": "Parent", "subscriber_last": "Lovelace", "subscriber_birth_date": "1950-01-01", "patient_first": "Ada", "patient_last": "Lovelace", "patient_birth_date": "1980-01-01"}
        body = stedi_request_body({"stedi_payer_id": "payer"}, {"rendering_npi": "1234567890", "organization_name": "Clinic"}, coverage, "2026-10-01", "98")
        self.assertEqual(body["subscriber"]["dateOfBirth"], "1950-01-01")
        self.assertEqual(body["dependent"]["dateOfBirth"], "1980-01-01")


if __name__ == "__main__":
    unittest.main()
