import unittest
from unittest.mock import patch

from app.api.routers.commercial_estimator import _appointment_readiness, _coverage_snapshot, _ecw_conflicts, _nmct_path, _past_visit_with_saved_estimate, _subscriber_identity, stedi_request_body
from app.insurance.commercial import CommercialError, estimate, normalize_stedi_271, parse_rate_csv
from app.insurance.ecw_backend import _patient_matches


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
            {"code": "B", "coverageLevel": "Individual", "serviceTypeCodes": ["98"], "benefitAmount": "40", "inPlanNetworkIndicatorCode": "Y", "authOrCertIndicator": "N", "additionalInformation": [{"description": "Referral not required"}]},
        ]}
        benefits = normalize_stedi_271(payload, "98", True)
        self.assertEqual(benefits["deductible_remaining_cents"], 50000)
        self.assertEqual(benefits["oop_remaining_cents"], 250000)
        self.assertEqual(benefits["copay_cents"], 4000)
        self.assertFalse(benefits["requires_review"])

    def test_stedi_family_or_missing_accumulator_requires_review(self):
        benefits = normalize_stedi_271({"benefitsInformation": [{"code": "1"}]}, "98", True)
        self.assertTrue(benefits["requires_review"])

    def test_stedi_does_not_assume_missing_referral_or_authorization_is_clear(self):
        payload = {"plans": [{"benefits": {"statuses": [{"status": "Active"}]}}], "benefitsInformation": [
            {"code": code, "coverageLevel": "Individual", "serviceTypeCodes": ["98"],
             "inPlanNetworkIndicatorCode": "Y", "timeQualifierCode": "29", "benefitAmount": amount}
            for code, amount in (("C", "0"), ("G", "1000"), ("B", "25"))]}
        benefits = normalize_stedi_271(payload, "98", True)
        self.assertEqual((benefits["authorization_signal"], benefits["referral_signal"]), ("unknown", "unknown"))
        self.assertTrue(benefits["requires_review"])

    def test_cardless_identity_and_ecw_coverage_feed_dependent_stedi_request(self):
        patient = {"name": [{"use": "official", "given": ["Ada", "B"], "family": "Lovelace"}], "birthDate": "1980-01-01"}
        profile = {"patient_first_name": "Ada", "patient_middle_initial": "B", "patient_last_name": "Lovelace", "patient_birth_date": "1980-01-01"}
        self.assertTrue(_patient_matches(patient, profile))
        self.assertFalse(_patient_matches(patient, {**profile, "patient_middle_initial": "C"}))
        inputs = {"patient": {"patient_first_name": "Ada", "patient_last_name": "Lovelace", "patient_birth_date": "1980-01-01"},
                  "subscriber": {"firstName": "Parent", "lastName": "Lovelace", "birthDate": "1950-01-01"},
                  "member_id": "M123", "relationship": "19", "payer_id": "bcbs", "service_date": "2026-10-01",
                  "rendering_provider": {"npi": "1234567890"}, "place_of_service": "11"}
        source = {"_patient_id": "patient-1", "coverage_id": "coverage-1", "appointment_id": "visit-1", "active_coverages": 1,
                  "location_id": "north", "billing_provider": {"npi": "1234567890"}}
        coverage = _coverage_snapshot(inputs, source)
        session = {"practice_id": "clinic", "intake": {"fields": {"relationship": "unknown"}}}
        case = {"commercial": {"payer_connection_id": "bcbs", "provider_network_id": "network", "service_date": "2026-10-01"}}
        with patch("app.api.routers.commercial_estimator._network", return_value={"billing_npi": "1234567890", "rendering_npi": "1234567890", "place_of_service": "11"}):
            self.assertEqual(_ecw_conflicts(case, session, inputs, source), [])
            session["intake"]["fields"]["relationship"] = "self"
            self.assertEqual(_ecw_conflicts(case, session, inputs, source), [])
            self.assertTrue(_ecw_conflicts(case, session, inputs, {**source, "active_coverages": 2}))
        body = stedi_request_body({"stedi_payer_id": "payer"}, {"rendering_npi": "1234567890", "organization_name": "Clinic"}, coverage, "2026-10-01", "98")
        self.assertEqual(body["subscriber"]["dateOfBirth"], "1950-01-01")
        self.assertEqual(body["dependent"]["dateOfBirth"], "1980-01-01")

    def test_stedi_fractional_coinsurance_and_authorization_are_not_mispriced(self):
        payload = {"plans": [{"benefits": {"statuses": [{"status": "Active"}]}}], "benefitsInformation": [
            {"code": "C", "coverageLevel": "Individual", "serviceTypeCodes": ["98"], "timeQualifierCode": "29", "benefitAmount": "500", "inPlanNetworkIndicatorCode": "Y"},
            {"code": "G", "coverageLevel": "Individual", "serviceTypeCodes": ["98"], "timeQualifierCode": "29", "benefitAmount": "2500", "inPlanNetworkIndicatorCode": "Y"},
            {"code": "A", "coverageLevel": "Individual", "serviceTypeCodes": ["98"], "benefitPercent": ".20", "authOrCertIndicator": "Y", "inPlanNetworkIndicatorCode": "Y"},
        ]}
        benefits = normalize_stedi_271(payload, "98", True)
        self.assertEqual(benefits["coinsurance_percent"], "20.00")
        self.assertEqual(benefits["authorization_signal"], "required")
        self.assertTrue(benefits["requires_review"])

    def test_dependent_uses_policyholder_birth_date_and_self_does_not_create_dependent(self):
        first, last, birth_date, is_self = _subscriber_identity(
            {"relationship": "child", "subscriber_name": "Parent Example", "subscriber_birth_date": "1970-01-01"},
            {"given_name": "Child", "family_name": "Example", "birth_date": "2010-01-01"},
        )
        self.assertEqual((first, last, birth_date, is_self), ("Parent", "Example", "1970-01-01", False))
        self.assertEqual(_subscriber_identity({}, {"given_name": "Pat", "family_name": "Example", "birth_date": "1980-01-01"})[3], True)

    def test_nmct_path_uses_ecw_payer_provider_location_and_pos(self):
        payer = {"id": "bcbs", "category": "commercial_medical", "status": "staff_guidance_enabled"}
        network = {"id": "network", "payer_connection_id": "bcbs", "status": "verified", "rendering_npi": "1234567893", "location_id": "north", "place_of_service": "11", "effective_from": "2026-01-01", "effective_to": None}
        profile = {"id": "office", "payer_connection_id": "bcbs", "provider_network_id": "network", "status": "staff_guidance_enabled"}
        with patch("app.api.routers.commercial_estimator.commercial_store.connection", return_value=payer), \
             patch("app.api.routers.commercial_estimator.commercial_store.list_networks", return_value=[network]), \
             patch("app.api.routers.commercial_estimator.commercial_store.list_profiles", return_value=[profile]):
            selected = _nmct_path("nmct", {"payer_id": "bcbs", "service_date": "2026-09-23", "rendering_provider": {"npi": "1234567893"}, "place_of_service": "11"}, {"location_id": "north"})
        self.assertEqual(selected, (payer, network, profile))

    def test_readiness_reports_independent_real_data_gaps_without_stedi(self):
        inputs = {"payer_id": None, "service_date": "2026-09-24", "lines": [],
                  "rendering_provider": {"npi": None}, "place_of_service": None,
                  "patient": {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"},
                  "subscriber": {}, "member_id": None, "relationship": None}
        source = {"payer": "Real eCW payer", "location_id": "office", "rendering_provider": {"npi": "1234567893"},
                  "active_coverages": 0, "coverage_id": None}
        with patch("app.api.routers.commercial_estimator.commercial_store.list_connections", return_value=[]), \
             patch("app.api.routers.commercial_estimator.commercial_store.list_networks", return_value=[]), \
             patch("app.api.routers.commercial_estimator.commercial_store.list_profiles", return_value=[]), \
             patch("app.api.routers.commercial_estimator.commercial_store.list_rate_versions", return_value=[]), \
             patch("app.api.routers.commercial_estimator.object_store.enabled", return_value=False), \
             patch.dict("app.api.routers.commercial_estimator.os.environ", {}, clear=True):
            result = _appointment_readiness("nmct", {"practice_id": "nmct", "intake": {"fields": {}}}, inputs, source)
        self.assertFalse(result["ready"])
        self.assertEqual(result["ecw"]["payer"], "Real eCW payer")
        self.assertTrue(any("Stedi payer ID" in item for item in result["blockers"]))
        self.assertTrue(any("provider-network" in item for item in result["blockers"]))
        self.assertTrue(any("contracted CPT" in item for item in result["blockers"]))
        self.assertTrue(any("CPT/HCPCS-coded" in item for item in result["blockers"]))
        self.assertFalse(any("rendering provider NPI" in item for item in result["blockers"]))

    def test_readiness_allows_real_configured_appointment_to_continue(self):
        payer = {"id": "bcbs", "category": "commercial_medical", "status": "staff_guidance_enabled", "rules": {"service_type_code": "98"}}
        network = {"id": "network", "payer_connection_id": "bcbs", "status": "verified", "billing_npi": "1234567893",
                   "rendering_npi": "1234567893", "location_id": "north", "place_of_service": "11",
                   "effective_from": "2026-01-01", "effective_to": None}
        profile = {"id": "office", "payer_connection_id": "bcbs", "provider_network_id": "network",
                   "status": "staff_guidance_enabled", "plan_matcher": ""}
        inputs = {"payer_id": "bcbs", "service_date": "2026-09-24", "lines": [{"code": "99204", "units": 1, "service_status": "scheduled"}],
                  "rendering_provider": {"npi": "1234567893"}, "place_of_service": "11",
                  "patient": {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"},
                  "subscriber": {"firstName": "Pat", "lastName": "Example", "birthDate": "1980-01-01"},
                  "member_id": "real-member", "relationship": "18"}
        source = {"payer": "BCBS", "location_id": "north", "billing_provider": {"npi": "1234567893"},
                  "active_coverages": 1, "coverage_id": "real-coverage"}
        with patch("app.api.routers.commercial_estimator.commercial_store.list_connections", return_value=[payer]), \
             patch("app.api.routers.commercial_estimator.commercial_store.list_networks", return_value=[network]), \
             patch("app.api.routers.commercial_estimator.commercial_store.list_profiles", return_value=[profile]), \
             patch("app.api.routers.commercial_estimator.commercial_store.list_rate_versions", return_value=[{"status": "active"}]), \
             patch("app.api.routers.commercial_estimator._ecw_coded_lines", return_value=([{"code": "99204", "units": 1, "allowed_cents": 25000}], [])), \
             patch("app.api.routers.commercial_estimator.object_store.enabled", return_value=True), \
             patch.dict("app.api.routers.commercial_estimator.os.environ", {"STEDI_EXECUTION_MODE": "commercial_pilot", "STEDI_LIVE_ELIGIBILITY_ENABLED": "true", "STEDI_API_KEY": "test-only-key"}):
            result = _appointment_readiness("nmct", {"practice_id": "nmct", "intake": {"fields": {}}}, inputs, source)
        self.assertTrue(result["ready"], result["blockers"])

    def test_history_only_shows_an_estimate_saved_for_the_same_ecw_visit(self):
        visit = {"id": "encounter-1", "date": "2026-09-22", "status": "finished", "coded_services": ["99204"]}
        case = {"source_mode": "commercial", "created_at": "2026-09-22T12:00:00+00:00",
                "commercial": {"ecw": {"appointment_id": "encounter-1"}},
                "estimate": {"allowed_cents": 25000, "patient_cents": 5000, "plan_cents": 20000,
                             "disclaimer": "Estimate only."}}
        self.assertEqual(_past_visit_with_saved_estimate(visit, case)["saved_estimate"]["patient_cents"], 5000)
        self.assertIsNone(_past_visit_with_saved_estimate({**visit, "id": "encounter-2"}, case)["saved_estimate"])


if __name__ == "__main__":
    unittest.main()
