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
        coverage.update(provider_network_status="in_network", provider_network="Confirmed by clinic contract matrix")
        result = stedi_estimate.calculate(coverage, [{"code": "99204", "description": "Visit", "units": "1", "allowed_cents": 10000}])
        self.assertEqual((result["estimate_available"], result["patient_cents"], result["allowed_cents"]), (True, 5200, 10000))

    def test_unconfirmed_or_out_of_network_never_returns_a_price(self):
        result = stedi_estimate.calculate({"active": True, "provider_network_status": "out_of_network", "plans": []}, [{"code": "99204", "units": "1", "allowed_cents": 10000}])
        self.assertFalse(result["estimate_available"])
        self.assertIsNone(result["patient_cents"])


if __name__ == "__main__":
    unittest.main()
