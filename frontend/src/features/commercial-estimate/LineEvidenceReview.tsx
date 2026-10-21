import { useState } from 'react'
import type { PrevisitEstimate, VisitReview } from '../../services/api/commercial-estimator'

type Save = (review: VisitReview) => Promise<boolean>

export function LineEvidenceReview({
  result,
  busy,
  save,
}: {
  result: PrevisitEstimate
  busy: boolean
  save: Save
}) {
  const lines = (result.codes || []).filter((row) => row.units && row.id)
  const [lineId, setLineId] = useState(lines[0]?.id || '')
  const [kind, setKind] = useState<'benefits' | 'authorization' | 'referral'>('benefits')
  const [status, setStatus] = useState<'valid' | 'not_required' | 'pending' | 'denied'>('valid')
  const [category, setCategory] = useState('')
  const [costShare, setCostShare] = useState<'copay' | 'coinsurance' | 'cumulative'>('coinsurance')
  const [deductible, setDeductible] = useState(true)
  const [selectedRows, setSelectedRows] = useState<string[]>([])
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const [from, setFrom] = useState(result.service_date)
  const [through, setThrough] = useState(result.service_date)
  const [error, setError] = useState('')
  const line = lines.find((row) => row.id === lineId) || lines[0]
  const appliesToLine = (row: { procedure_code?: string | null }, code: string) =>
    row.procedure_code === line?.code ||
    !line?.benefit_service_types?.length ||
    line.benefit_service_types.includes(code)
  const categories = [
    ...new Set(
      (result.benefits?.reported_options || [])
        .filter(
          (row) =>
            row.id &&
            (row.kind === 'copay' || row.kind === 'coinsurance') &&
            (!row.procedure_code || row.procedure_code === line?.code),
        )
        .flatMap(
          (row) =>
            row.service_type_code
              ?.split(',')
              .map((code) => code.trim())
              .filter((code) => code && appliesToLine(row, code)) || [],
        ),
    ),
  ]
  const activeCategory = categories.includes(category) ? category : categories[0] || ''
  const options = (result.benefits?.reported_options || []).filter(
    (row) =>
      row.id &&
      (row.kind === 'copay' || row.kind === 'coinsurance') &&
      (!row.procedure_code || row.procedure_code === line?.code) &&
      appliesToLine(row, activeCategory) &&
      row.service_type_code
        ?.split(',')
        .map((code) => code.trim())
        .includes(activeCategory),
  )
  if (!lines.length) return null
  const submit = async () => {
    if (
      !line?.id ||
      reference.trim().length < 3 ||
      notes.trim().length < 10 ||
      !from ||
      !through ||
      from > through ||
      !(from <= result.service_date && result.service_date <= through)
    ) {
      setError('Record a payer reference, review notes and dates covering this service date.')
      return
    }
    const wanted = costShare === 'cumulative' ? ['copay', 'coinsurance'] : [costShare]
    const picked = options.filter((row) => row.id && selectedRows.includes(row.id))
    if (
      kind === 'benefits' &&
      (picked.length !== wanted.length ||
        !wanted.every((name) => picked.some((row) => row.kind === name)))
    ) {
      setError('Select the payer row for each verified cost-share term.')
      return
    }
    const saved = await save({
      verification: {
        line_id: line.id,
        kind,
        status: kind === 'benefits' ? 'valid' : status,
        reference: reference.trim(),
        notes: notes.trim(),
        valid_from: from,
        valid_to: through,
        units: Number(line.units),
        ...(kind === 'benefits'
          ? {
              service_type_code: activeCategory,
              cost_share: costShare,
              deductible_applies: deductible,
              selected_ids: selectedRows,
            }
          : {}),
      },
    })
    if (saved) setError('')
  }
  return (
    <section className="cash-package-options" aria-labelledby="line-evidence-review-title">
      <h3 id="line-evidence-review-title">Review evidence for each selected service</h3>
      <p className="visit-muted">
        Use saved payer rows when they clearly apply. Record unresolved member-specific benefits,
        referrals or authorization decisions for each line. A public policy is not an approval.
      </p>
      <label>
        Selected service
        <select
          value={line?.id}
          onChange={(event) => {
            setLineId(event.target.value)
            setSelectedRows([])
          }}
        >
          {lines.map((row) => (
            <option key={row.id} value={row.id}>
              {row.code} · {row.units} unit(s)
            </option>
          ))}
        </select>
      </label>
      <label>
        Decision
        <select
          value={kind}
          onChange={(event) => {
            setKind(event.target.value as typeof kind)
            setSelectedRows([])
          }}
        >
          <option value="benefits">Medical benefit and cost share</option>
          <option value="authorization">Prior authorization</option>
          <option value="referral">Referral</option>
        </select>
      </label>
      {kind === 'benefits' ? (
        <>
          <label>
            Payer-confirmed service category
            <select
              value={activeCategory}
              onChange={(event) => {
                setCategory(event.target.value)
                setSelectedRows([])
              }}
            >
              {categories.map((code) => (
                <option key={code} value={code}>
                  Service category {code}
                </option>
              ))}
            </select>
          </label>
          <label>
            Applicable cost share
            <select
              value={costShare}
              onChange={(event) => {
                setCostShare(event.target.value as typeof costShare)
                setSelectedRows([])
              }}
            >
              <option value="coinsurance">Coinsurance</option>
              <option value="copay">Copay</option>
              <option value="cumulative">Both terms apply</option>
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={deductible}
              onChange={(event) => setDeductible(event.target.checked)}
            />
            Payer confirmed deductible applies to this line
          </label>
          <p className="visit-muted">
            Select only returned rows confirmed for this code, product and visit.
          </p>
          {options.map((row) => (
            <label key={row.id}>
              <input
                type="checkbox"
                checked={!!row.id && selectedRows.includes(row.id)}
                onChange={(event) =>
                  setSelectedRows((current) =>
                    event.target.checked
                      ? [...current, row.id!]
                      : current.filter((id) => id !== row.id),
                  )
                }
              />
              {row.kind} · {row.service_type_code} · {row.procedure_code || 'category'} · {row.id}
            </label>
          ))}
          {!options.length && (
            <p className="visit-muted">
              No matching payer cost-share row. Obtain service-specific benefit evidence before
              pricing.
            </p>
          )}
        </>
      ) : (
        <label>
          Member-specific decision
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as typeof status)}
          >
            <option value="valid">Approved / on file</option>
            <option value="not_required">Not required</option>
            <option value="pending">Pending</option>
            <option value="denied">Denied</option>
          </select>
        </label>
      )}
      <label>
        Payer reference or Decision ID
        <input
          value={reference}
          maxLength={500}
          onChange={(event) => setReference(event.target.value)}
        />
      </label>
      <label>
        What the payer confirmed
        <textarea
          value={notes}
          maxLength={2000}
          onChange={(event) => setNotes(event.target.value)}
        />
      </label>
      <label>
        Valid from{' '}
        <input type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
      </label>
      <label>
        Valid through{' '}
        <input type="date" value={through} onChange={(event) => setThrough(event.target.value)} />
      </label>
      {error && (
        <p className="visit-error" role="alert">
          {error}
        </p>
      )}
      <button type="button" disabled={busy} onClick={() => void submit()}>
        Save line verification
      </button>
    </section>
  )
}
