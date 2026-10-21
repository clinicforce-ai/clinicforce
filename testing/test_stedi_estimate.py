"""Small safety checks for the temporary Stedi local estimate path."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.insurance import stedi_estimate, stedi_results


class StediEstimateTests(unittest.TestCase):
    def test_confirmed_in_network_deductible_and_coinsurance_uses_clinic_allowed_rate(self):
        coverage = stedi_results.coverage({"id": "check-1", "plans": [{"benefits": {
            "statuses": [{"status": "Active Coverage", "planCoverageDescription": "Example Plan"}],
            "deductible": [{"remaining": "40.00", "network": "Y"}],
            "coInsurance": [{"percent": "20", "network": "Y"}],
            "outOfPocket": [{"remaining": "500.00", "network": "Y"}],
        }}]})
        coverage.update(provider_network_status="in_network", provider_network="Confirmed by clinic contract matrix",
                        identity={"status": "matched", "match_version": 2, "binding": "synthetic-patient-binding"})
        result = stedi_estimate.calculate(coverage, [{"code": "99204", "description": "Visit", "units": "1", "allowed_cents": 10000}])
        self.assertEqual((result["estimate_available"], result["patient_cents"], result["allowed_cents"]), (True, 5200, 10000))

    def test_in_network_benefits_for_another_member_do_not_produce_a_price(self):
        coverage = {"active": True, "provider_network_status": "in_network",
                    "identity": {"status": "review", "match_version": 2, "binding": None,
                                 "differences": ["first_name", "last_name"],
                                 "policy_differences": ["subscriber_first"], "missing_fields": []},
                    "plans": [{"benefits": [{"category": "Copayment", "network": "in_network",
                                            "amount_cents": 2500}]}]}
        result = stedi_estimate.calculate(coverage, [{"code": "99204", "units": "1", "allowed_cents": 10000}])
        self.assertFalse(result["estimate_available"])
        self.assertIsNone(result["patient_cents"])
        self.assertIn("first name, last name, subscriber first", result["message"])

    def test_conflicting_office_copays_never_depend_on_payer_row_order(self):
        benefits = [{"category": "Copayment", "network": "in_network", "level": "INDIVIDUAL",
                     "amount_cents": amount} for amount in (2500, 0)]
        coverage = {"active": True, "provider_network_status": "in_network",
                    "identity": {"status": "matched", "match_version": 2, "binding": "synthetic-patient-binding"},
                    "plans": [{"benefits": [*benefits, {"category": "Out-of-pocket maximum",
                                 "network": "in_network", "level": "INDIVIDUAL",
                                 "remaining_cents": 90000}]}]}
        for rows in (benefits, list(reversed(benefits))):
            coverage["plans"][0]["benefits"][:2] = rows
            result = stedi_estimate.calculate(coverage, [{"code": "99204", "units": "1", "allowed_cents": 10000}])
            self.assertFalse(result["estimate_available"])
            self.assertIsNone(result["patient_cents"])
            self.assertIn("conflicting in-network copay rows", result["message"])

    def test_family_deductible_cannot_price_an_individual_service(self):
        coverage = {"active": True, "provider_network_status": "in_network",
                    "identity": {"status": "matched", "match_version": 2, "binding": "synthetic-patient-binding"},
                    "plans": [{"benefits": [
                        {"category": "Deductible", "network": "in_network", "level": "FAMILY", "remaining_cents": 0},
                        {"category": "Coinsurance", "network": "in_network", "level": "INDIVIDUAL", "percent": 20},
                        {"category": "Out-of-pocket maximum", "network": "in_network", "level": "INDIVIDUAL", "remaining_cents": 90000},
                    ]}]}
        result = stedi_estimate.calculate(coverage, [{"code": "99204", "units": "1", "allowed_cents": 10000}])
        self.assertFalse(result["estimate_available"])
        self.assertIsNone(result["patient_cents"])

    def test_unconfirmed_or_out_of_network_never_returns_a_price(self):
        result = stedi_estimate.calculate({"active": True, "provider_network_status": "out_of_network", "plans": []}, [{"code": "99204", "units": "1", "allowed_cents": 10000}])
        self.assertFalse(result["estimate_available"])
        self.assertIsNone(result["patient_cents"])


if __name__ == "__main__":
    unittest.main()
