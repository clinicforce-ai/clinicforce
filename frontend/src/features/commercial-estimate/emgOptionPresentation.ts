import type { PrevisitEstimate } from '../../services/api/commercial-estimator'

export function emgOptionPresentation(
  option: PrevisitEstimate['emg_option'],
  costSupported: boolean,
  includedInQuote: boolean,
) {
  if (!option) return option
  if (includedInQuote)
    return {
      ...option,
      message:
        'Selected EMG / nerve testing CPTs are included in the visit collection quote. Final charges follow the testing performed and processed claim.',
    }
  if (costSupported) return option
  return {
    ...option,
    additional_min_cents: null,
    additional_max_cents: null,
    total_min_cents: null,
    total_max_cents: null,
    message: 'Recalculate the testing scenario after resolving the payer or visit issue above.',
  }
}
