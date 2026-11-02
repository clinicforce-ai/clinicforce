# First-milestone scenario matrix

| Scenario | Deterministic assertion | Test |
| --- | --- | --- |
| Office copay-only | $40 replaces office deductible/coinsurance, one group | `test_visit_collection_model.py::test_office_copay_only` |
| Independent office + diagnostic-day copays | $40 + one $75 across two diagnostic CPTs = $115 | `test_office_plus_one_diagnostic_day_copay_for_two_cpts` |
| Encounter-wide copay | Same visit-scoped copay ID across groups counted once | `test_one_encounter_copay_covering_office_and_procedure_is_not_added_twice` |
| Office + benchmark diagnostic share | $40 + $100 deductible + 20% × $183.47 = $176.69 | `test_office_plus_shared_deductible_and_coinsurance` |
| Applicable OOP remaining | $25 caps covered $115 visit; staff note cannot establish zero | `test_verified_oop_caps_visit_but_unverified_note_does_not` |
| Mixed synthetic 271 | $80 specialist selected over $40 general; 0% office over 30% general; OOP annual != remaining | `test_mixed_271_selects_specialist_not_pcp_or_general_coinsurance` |
| Missing policy | Planning $176.69, collection null | `test_planning_amount_is_not_permission_to_collect` |
| Missing benchmark row | No fabricated procedure total | `test_missing_reference_cpt_never_invents_price` |
| Identity conflict | No authorized positive amount | `test_identity_conflict_cannot_authorize_positive` |
| Independent acquisition during identity conflict | Missing BCBSTX CPT still becomes a clinic task while the patient's benefits remain unusable | `test_identity_conflict_still_identifies_independent_reference_gap` |
| Backend save/reload | Guidance and summary share amount; saved read retains approved rule | `test_saved_guidance_and_summary_use_one_authoritative_visit_amount`, `test_saved_case_reload_retains_approved_visit_policy` |
| Sonnet 5 | Model ID, cited finding, no patient name in prompt, no monetary authority | `test_sonnet_finding_is_cited_and_cannot_change_money` |
| Typed research read | One bounded selected-benefit tool response; no patient name in messages | `test_sonnet_tool_reads_only_selected_saved_fact` |
| Source-backed combined patterns | Deductible then copay; copay plus coinsurance; distinct copay and deductible components | `test_explicit_source_backed_combined_interactions` |
| Unsupported combined pattern | No selected interaction source means no invented total | `test_combined_interaction_without_selected_source_stays_unresolved` |
| Canonical evidence | Selected fact scope, identity and benefit conflict, payment reference, no patient name | `test_canonical_evidence_preserves_conflict_and_selected_source_without_patient_identity` |
| Policy formula and restrictions | Percentage, cap and selected CPT exclusion | `test_policy_percentage_and_exclusion_are_applied_after_modeling` |
| Policy and payment gate | Disclosure and referenced prior-payment verification required | `test_policy_requires_disclosure_and_verified_payment_reference` |
| Unknown/family OOP | Unknown requires explicit policy opt-in; verified applicable family exhaustion yields zero | `test_unknown_oop_requires_exact_policy_opt_in_for_positive_collection`, `test_verified_applicable_family_oop_zero_caps_once` |
| Protected collection | Verified QMB check-in $0 remains distinct from modeled share | `test_verified_protection_is_zero_collection_not_zero_modeled_share` |
| Scoped reference | Expired/POS/provider-mismatched BCBSTX row does not price another visit; acquisition task survives a benefit conflict | `test_reference_scope_and_price_acquisition_remain_visible_during_benefit_conflict`, `test_expired_reference_version_is_not_used_for_new_service_date`, `test_provider_scope_change_invalidates_reference` |
| Aetna reference fallback | New path replaces an Aetna portal planning reference with scoped BCBSTX benchmark | `test_v2_aetna_uses_bcbstx_reference_even_when_legacy_portal_reference_exists` |
| Clinic acquisition worklist | Missing benchmark CPT is deduplicated across payers without patient text; malformed saved modifiers are ignored | `test_benchmark_acquisition_is_clinic_level_and_deduplicated_across_payers`, `test_malformed_saved_modifier_cannot_break_admin_inventory` |
| React | Backend $176.69 displayed; pending planning $176.69 does not occupy collection slot; group evidence, copay frequency and deductible applicability render | `frontend/visit-collection-ui.test.cjs` |
| Disposable PostgreSQL | Two approved visit scopes persist as separate policy versions under one route | `test_collection_policy_config.py::test_visit_policies_persist_as_separate_scoped_versions` |
| Saved case and event snapshot | Visit decision survives a PostgreSQL case save/reload and matches the event snapshot | `test_visit_collection_model.py::test_visit_decision_is_saved_and_reloaded_with_snapshot` |
| Eight fake October visits | Invented patients cover specialist 271 conflict, combined copays, deductible/coinsurance, OOP cap, Cigna and Aetna planning-only references, identity conflict, and missing CPT benchmark | `test_october_synthetic_staging.py`; 13 passed with disposable PostgreSQL, including staging-admin seed route, FastAPI saved read, event snapshots and production/role/flag guards |

The existing backend suite tests the broader eCW/Stedi/FastAPI route, saved cases and historical office benefits. The full backend run passed 1,568 tests with 86 skipped; a focused rerun after the final independent-acquisition change passed 35 with one database skip. Two PostgreSQL tests passed using a disposable isolated cluster. React rendering passed 11 assertions; lint, formatting, build/type and source size passed. The [69-case counterfactual](../../../contextforcodex/collection-engine-october-replay-2026-11-02.md) ran against saved original normalized evidence and current scoped rates. The eight fake visits now pass a combined local PostgreSQL, admin seed and FastAPI saved-read test. Their marked saved-read path uses synthetic prices and policies from the case and makes no eCW, Stedi or Bedrock call; it does not validate live Library refresh. **It is not a deployed staging test.** The next gate is to run the authenticated seed in the existing staging NMCT practice and inspect the saved FastAPI response and React display. Synthetic approvals apply only to fake patient/payer/product IDs and cannot be treated as real NMCT authority. Staging still lacks live eCW and the deployed collection-engine flag. Exact historical config reconstruction and real NMCT visit-policy approval remain open.
