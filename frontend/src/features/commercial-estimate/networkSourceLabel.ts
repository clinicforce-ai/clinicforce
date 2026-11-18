const labels: Record<string, string> = {
  clinic_library_contract: 'Current Clinical Library contract',
  clinic_attested_cigna_ppo:
    'Clinic attestation for matched Cigna PPO office provider · not payer confirmed',
  clinic_attested_aetna_office:
    'Clinic attestation for matched Aetna Choice POS II group and office provider · not payer confirmed',
  clinic_library_absent: 'No contract linked to this exact payer product and provider',
  clinic_no_insurer_contract: 'No current insurer contract registered in the NMCT Clinical Library',
  clinic_nonparticipation_rule: 'Current scoped clinic nonparticipation rule',
  clinic_contract_inventory: 'Certified clinic contract inventory · not payer-confirmed',
}

export function networkSourceLabel(
  decision: string | undefined,
  source: string | null | undefined,
  fallback: string | null | undefined,
) {
  return decision === 'in_network_contract_supported'
    ? 'Current signed clinic agreement · review member product and clinician tier'
    : (source && labels[source]) || fallback
}
