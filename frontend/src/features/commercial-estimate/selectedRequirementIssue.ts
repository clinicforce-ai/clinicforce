import type { PrevisitEstimate } from '../../services/api/commercial-previsit'
import { requirementForLine } from './requirementForLine'

export function selectedRequirementIssue(
  result: PrevisitEstimate | null | undefined,
): string | null {
  if (!result || result.price_range?.mode !== 'services') return null
  const lines = result.price_range.lines || []
  if (!lines.length) return null
  const payer = result.payer || 'the payer'
  for (const kind of ['referral', 'authorization'] as const) {
    const label = kind === 'referral' ? 'referral' : 'prior authorization'
    const items = lines.map((line) => ({ line, rule: line.benefits?.requirements?.[kind] }))
    const documented = result.requirement_evidence?.[kind]
    const cleared = (rule: (typeof items)[number]['rule']) =>
      rule?.required === 'required' &&
      rule.cleared === true &&
      rule.status === 'valid' &&
      !!rule.reference &&
      !!rule.verified_at
    const required = items.filter(({ rule }) => rule?.required === 'required')
    const codes = (rows: typeof items) => [...new Set(rows.map(({ line }) => line.code))].join(', ')
    for (const { line, rule } of items) {
      const decision = requirementForLine(
        result.requirement_evidence?.procedures,
        line.id,
        line.code,
      )?.[kind]
      if (
        (decision?.status === 'Required' && rule?.required === 'not_required') ||
        (decision?.status === 'Not required' && rule?.required === 'required')
      ) {
        return `The saved ${label} rule for CPT ${line.code} conflicts with its service line. Ask ${payer} which rule applies, correct the clinic evidence and rerun the insured estimate.`
      }
      if (
        kind === 'authorization' &&
        decision?.status === 'Required' &&
        decision.approval_status &&
        decision.approval_status !== 'Verified' &&
        cleared(rule)
      ) {
        return `The saved approval status for CPT ${line.code} conflicts with its authorization reference. Check the authorization decision with ${payer}, correct the approval record and rerun the insured estimate.`
      }
      if (decision?.status === 'Required' && !cleared(rule)) {
        return `Check the ${label} for CPT ${line.code} with ${payer}, record its valid reference and verification time, then rerun the insured estimate.`
      }
    }
    if (documented?.status === 'Not required' && required.length) {
      return `The saved ${label} summary conflicts with the CPT ${codes(required)} rule. Ask ${payer} which rule applies, correct the clinic evidence and rerun the insured estimate.`
    }
    const unresolved = items.find(
      ({ rule }) =>
        ['denied', 'expired', 'pending'].includes(rule?.status || '') ||
        (rule?.required === 'required' && !cleared(rule)),
    )
    if (unresolved) {
      return `Check the ${label} for CPT ${unresolved.line.code} with ${payer}, record its valid reference and verification time, then rerun the insured estimate.`
    }
    if (documented?.status === 'Required') {
      const requiredDecisions =
        result.requirement_evidence?.procedures?.filter(
          (row) => row[kind]?.status === 'Required',
        ) || []
      const relevant = requiredDecisions.length
        ? items.filter(
            ({ line }) =>
              requirementForLine(result.requirement_evidence?.procedures, line.id, line.code)?.[
                kind
              ]?.status === 'Required',
          )
        : items
      const unmatchedDecision = requiredDecisions.some(
        (row) =>
          !relevant.some(
            ({ line }) =>
              row.code === line.code && (!row.id || row.id === row.code || row.id === line.id),
          ),
      )
      if (
        (kind === 'authorization' && documented.approval_status !== 'Verified') ||
        unmatchedDecision ||
        !relevant.length ||
        relevant.some(({ rule }) => !cleared(rule))
      ) {
        return `Obtain the required ${label} for CPT ${codes(relevant.length ? relevant : items)} from ${payer}, record its reference and verification time, then rerun the insured estimate.`
      }
    }
    if (
      kind === 'referral' &&
      result.benefits?.referral_signal === 'required' &&
      documented?.status !== 'Required' &&
      (!items.length || items.some(({ rule }) => !cleared(rule)))
    ) {
      return `Check the payer-reported referral requirement for CPT ${codes(items)} with ${payer}, record its clearance and rerun the insured estimate.`
    }
  }
  return null
}
