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
| Backend save/reload | Guidance and summary share amount; saved read retains approved rule | `test_saved_guidance_and_summary_use_one_authoritative_visit_amount`, `test_saved_case_reload_retains_approved_visit_policy` |
| Sonnet 5 | Model ID, cited finding, no patient name in prompt, no monetary authority | `test_sonnet_finding_is_cited_and_cannot_change_money` |
| React | Backend $176.69 displayed; pending planning $176.69 does not occupy collection slot | `frontend/visit-collection-ui.test.cjs` |
| Disposable PostgreSQL | Two approved visit scopes persist as separate policy versions under one route | `test_collection_policy_config.py::test_visit_policies_persist_as_separate_scoped_versions` |
| Saved case and event snapshot | Visit decision survives a PostgreSQL case save/reload and matches the event snapshot | `test_visit_collection_model.py::test_visit_decision_is_saved_and_reloaded_with_snapshot` |

The existing backend suite tests the broader eCW/Stedi/FastAPI route, saved cases and historical office benefits. Both new disposable PostgreSQL tests passed. This milestone has no live authorized eCW Eligibility Insights probe, production read-only cohort measurement, or full 69-case original-evidence replay; those are acceptance gaps, not passing tests.
