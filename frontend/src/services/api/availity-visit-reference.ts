export type AvailityVisitReference = {
  source: string
  service_date: string
  active: true
  network_status: 'in_network'
  plan: string
  copay_cents: number
  coinsurance_percent: number
  oop_annual_cents: number
  oop_remaining_cents: number
}
