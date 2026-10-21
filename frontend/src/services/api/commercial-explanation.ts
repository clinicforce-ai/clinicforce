export type PaymentExplanation = {
  sentence: string
  reason_code: string
  network_tier: string
  pricing_basis: string
  unpriced_codes: string[]
  plan_name?: string | null
  network_decision?: string | null
  network_source?: string | null
  contract_evidence?: {
    document_id?: string
    page?: number
    locator?: string
    scope?: Record<string, unknown>
  }
  selected_services?: Array<{
    line_id?: string
    code?: string
    units?: number
    allowed_cents?: number | null
    allowed_basis?: string
    rate_source?: string
    rate_version?: string
    benefit_ids?: string[]
  }>
  payments_verified_cents?: number | null
  estimated_liability_cents?: number | null
  collect_now_cents?: number | null
  collection_policy_basis?: string
  financial_components?: Record<string, number | null> | null
  coinsurance_percent?: string | null
  coinsurance_base_cents?: number | null
  known_amount_only: boolean
  notice?: string | null
}
