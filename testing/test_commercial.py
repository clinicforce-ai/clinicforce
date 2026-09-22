import unittest

from app.insurance.commercial import CommercialError, estimate, normalize_stedi_271, parse_rate_csv


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
            {"code": "B", "coverageLevel": "Individual", "serviceTypeCodes": ["98"], "benefitAmount": "40", "inPlanNetworkIndicatorCode": "Y"},
        ]}
        benefits = normalize_stedi_271(payload, "98", True)
        self.assertEqual(benefits["deductible_remaining_cents"], 50000)
        self.assertEqual(benefits["oop_remaining_cents"], 250000)
        self.assertEqual(benefits["copay_cents"], 4000)
        self.assertFalse(benefits["requires_review"])

    def test_stedi_family_or_missing_accumulator_requires_review(self):
        benefits = normalize_stedi_271({"benefitsInformation": [{"code": "1"}]}, "98", True)
        self.assertTrue(benefits["requires_review"])


if __name__ == "__main__":
    unittest.main()
