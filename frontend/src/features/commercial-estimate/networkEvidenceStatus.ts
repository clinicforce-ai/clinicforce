import type { PrevisitEstimate } from '../../services/api/commercial-estimator'

export function networkEvidenceStatus(result: PrevisitEstimate | null) {
  const benefits = result?.benefits
  const price = result?.price_range
  const participationConflict =
    benefits?.network_conflict === true ||
    benefits?.review_reasons?.some((reason) =>
      reason.toLowerCase().includes('provider-network evidence reports nonparticipation'),
    ) === true
  const networkConfirmed =
    benefits?.network_status === 'in_network' &&
    !participationConflict &&
    [
      'clinic_library_contract',
      'clinic_group_agreement',
      'payer_participation_message',
      'clinic_attested_phcs_roster',
    ].includes(benefits.network_source || '')
  const outOfNetworkConfirmed =
    benefits?.network_status === 'out_of_network' &&
    [
      'payer_participation_message',
      'clinic_nonparticipation_rule',
      'clinic_contract_inventory',
      'clinic_no_insurer_contract',
    ].includes(benefits.network_source || '') &&
    !participationConflict
  const outOfNetworkAllowance =
    outOfNetworkConfirmed &&
    price?.mode === 'services' &&
    price.complete === true &&
    !!price.lines?.length &&
    price.lines.every(
      (line) =>
        ['payer_out_of_network', 'cms_original_medicare_estimate'].includes(
          line.allowed_basis || '',
        ) &&
        Number.isInteger(line.allowed_cents) &&
        line.allowed_cents! >= 0 &&
        line.rate_resolution?.status === 'RESOLVED' &&
        !!line.calculation,
    )
  return { participationConflict, networkConfirmed, outOfNetworkConfirmed, outOfNetworkAllowance }
}
