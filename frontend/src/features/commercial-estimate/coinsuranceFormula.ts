import type { PrevisitEstimate } from '../../services/api/commercial-previsit'
import { money } from './format'

export function feeProxyFormula(
  proxy: NonNullable<PrevisitEstimate['fee_proxy_estimate']>,
  payments = 0,
) {
  const appliedPayments = Math.min(Math.max(0, payments), proxy.patient_share_cents)
  const coinsurance = proxy.lines?.length
    ? proxy.lines
        .map(
          (line) =>
            `(${money(line.reference_cents)} × ${proxy.coinsurance_percent}% = ${money(line.coinsurance_cents)})`,
        )
        .join(' + ')
    : money(proxy.coinsurance_cents)
  const total = `${money(proxy.copay_cents)} copay + ${money(proxy.deductible_cents)} deductible + ${coinsurance}${proxy.oop_reduction_cents ? ` − ${money(proxy.oop_reduction_cents)} out-of-pocket cap` : ''}${appliedPayments ? ` − ${money(appliedPayments)} already paid` : ''} = ${money(proxy.patient_share_cents - appliedPayments)}.`
  return { coinsurance, total }
}

export function coinsuranceFormula(result: PrevisitEstimate): string | null {
  const summary = result.encounter_summary
  if (!summary) return null
  const total = summary.components?.coinsurance_cents
  if (total == null || total <= 0) return null

  const rate = Number(summary.coinsurance_percent)
  const base = summary.coinsurance_base_cents
  if (
    base != null &&
    summary.coinsurance_percent != null &&
    Number.isFinite(rate) &&
    rate >= 0 &&
    rate <= 100 &&
    Math.round((base * rate) / 100) === total
  ) {
    return `${rate.toLocaleString()}% of ${money(base)} after deductible`
  }
  if (
    summary.coinsurance_rounded_per_line &&
    base == null &&
    summary.coinsurance_percent != null &&
    Number.isFinite(rate) &&
    rate > 0 &&
    rate <= 100
  ) {
    return `${rate.toLocaleString()}% applied and rounded separately for each CPT after deductible`
  }

  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  const payable = lines.filter((line) => (line.calculation?.coinsurance_cents ?? 0) > 0)
  if (!payable.length) return null
  const rates = payable.map((line) => Number(line.calculation?.coinsurance_percent))
  const bases = payable.map((line) => line.calculation?.coinsurance_base_cents)
  const shares = payable.map((line) => line.calculation?.coinsurance_cents ?? 0)
  if (
    rates.some((value) => !Number.isFinite(value) || value < 0 || value > 100) ||
    new Set(rates).size !== 1 ||
    bases.some((value) => value == null) ||
    shares.reduce((sum, value) => sum + value, 0) !== total
  ) {
    return null
  }
  const payableBase = bases.reduce<number>((sum, value) => sum + (value ?? 0), 0)
  if (Math.round((payableBase * rates[0]) / 100) !== total) return null
  return `${rates[0].toLocaleString()}% of ${money(payableBase)} after deductible for CPTs with payable coinsurance`
}
