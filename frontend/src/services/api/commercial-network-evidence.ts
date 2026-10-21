export type CommercialNetworkEvidence = {
  outcome?:
    | 'IN_NETWORK_VERIFIED'
    | 'IN_NETWORK_CONTRACT_SUPPORTED'
    | 'OUT_OF_NETWORK_VERIFIED'
    | 'OUT_OF_NETWORK_CONTRACT_SUPPORTED'
    | 'UNRESOLVED'
  decision?:
    | 'confirmed_participating'
    | 'confirmed_nonparticipating'
    | 'in_network_contract_supported'
    | 'excluded_under_available_agreement'
    | 'lacking_applicable_network_evidence'
    | 'out_of_network_by_clinic_contract_inventory'
    | 'conflicting_network_evidence'
  source?: string
  evidence_ids?: string[]
  product_kind?: string
  page?: number
  passage?: string
  network_name?: string
  product?: string
  product_network_source?: string
  product_network_reference?: string
  provider_participation?: 'unresolved'
  billing_npi?: string
  rendering_npi?: string
  tin?: string
  location_id?: string
  place_of_service?: string
  service_date?: string
  document_sha256?: string
  product_source?: string
  product_mapping_approval?: {
    reference?: string
    actor_id?: string
    verified_at?: string
    valid_from?: string
    valid_to?: string
  }
  evidence_reference?: string
  effective_from?: string
  effective_to?: string
  document_id?: string
  document_label?: string
  locator?: string
  related_document_id?: string
  related_document_label?: string
  reason?: string
  approved_by?: string
  approved_at?: string
  reference?: string
  document_hashes?: Record<string, string>
  scope?: Record<string, string | null>
}
