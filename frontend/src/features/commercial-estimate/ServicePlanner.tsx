import { RequirementDecision } from './RequirementDecision'
import { BotoxClinicalReview } from './BotoxClinicalReview'
import { requirementForLine } from './requirementForLine'
import { requirementTag } from './plannerRequirementTag'
import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, Minus, Plus } from 'lucide-react'
import type {
  PrevisitEstimate,
  VisitCode,
  VisitReview,
} from '../../services/api/commercial-estimator'
import type { BotoxReview } from '../../services/api/commercial-visit-types'
import { clinicalName, groupFor, money } from './service-code'

type Save = (review: VisitReview) => Promise<boolean>
const groupOf = (row: VisitCode) => row.service_group || groupFor(row.code)

export function ServicePlanner({
  result,
  busy,
  save,
  viewEstimate,
}: {
  result: PrevisitEstimate
  busy: boolean
  save: Save
  viewEstimate: (message?: string) => void
}) {
  const available = (result.available_codes || result.codes).filter(
    (row, index, rows) =>
      !row.id || rows.findIndex((candidate) => candidate.id === row.id) === index,
  )
  const defaults =
    result.service_selection_status === 'approved'
      ? result.selected_ids || []
      : result.service_plan?.recommended_ids || []
  const [selected, setSelected] = useState<string[]>(defaults)
  const [quantities, setQuantities] = useState<Record<string, number>>(() =>
    Object.fromEntries(
      available.map((row) => [row.id!, Number(row.units) || (row.code.startsWith('J') ? 0 : 1)]),
    ),
  )
  const [cashSelection, setCashSelection] = useState(result.cash_quote?.selection || '')
  const [changed, setChanged] = useState(false)
  const [botox, setBotox] = useState<BotoxReview>(result.botox_review || {})
  const [editError, setEditError] = useState('')
  const autoSave = useRef<(continueToCollection: boolean) => Promise<void>>(async () => {})
  const lastAutoAttempt = useRef('')
  const emgVisit =
    result.service_plan?.kind === 'emg_ncs' ||
    result.provisional_scenario?.kind === 'ncs_study_count'
  const botoxVisit =
    result.service_plan?.kind === 'botox' ||
    available.some(
      (row) =>
        selected.includes(row.id!) &&
        ['botulinum_drug', 'chemodenervation'].includes(row.service_family || ''),
    )
  const [category, setCategory] = useState(emgVisit ? 'nerve' : botoxVisit ? 'other' : 'visit')
  const chosen = available.filter((row) => selected.includes(row.id!))
  const scenarioAlternatives = result.provisional_scenario?.candidate_ids || []
  const scenarioRules = scenarioAlternatives.map((id) => {
    const row = available.find((candidate) => candidate.id === id)
    return row && requirementForLine(result.requirement_evidence?.procedures || [], id, row.code)
  })
  const scenarioRule = (kind: 'referral' | 'authorization') => {
    const statuses = scenarioRules.map((rule) => rule?.[kind]?.status)
    return statuses.length &&
      statuses.every((status) => status === statuses[0]) &&
      statuses[0] !== 'Verification required'
      ? statuses[0]
      : 'Plan-specific rule not established for all alternatives'
  }
  const nerve = available
    .filter((row) => groupOf(row) === 'nerve')
    .sort((a, b) => a.code.localeCompare(b.code))
  const muscle = available.filter((row) => ['95885', '95886'].includes(row.code))
  const office = available
    .filter(
      (row) =>
        groupOf(row) === 'visit' &&
        (result.visit_type === 'new'
          ? Number(row.code) < 99210
          : result.visit_type === 'established'
            ? Number(row.code) > 99210
            : true),
    )
    .sort((a, b) => a.code.localeCompare(b.code))
  // CMS retired these two codes in 2014; retain existing selections only so staff can remove them.
  const retired = (row: VisitCode) =>
    result.service_date >= '2014-01-01' && ['64613', '64614'].includes(row.code)
  const other = available.filter(
    (row) =>
      (groupOf(row) === 'other' || row.code === '95887') &&
      (!retired(row) || selected.includes(row.id!)),
  )
  const update = (ids: string[]) => {
    setSelected(ids)
    setChanged(true)
    setCashSelection('')
    setEditError('')
  }
  const pickOne = (rows: VisitCode[], id: string) => {
    let ids = selected.filter((key) => !rows.some((row) => row.id === key))
    if (rows === nerve && !id)
      ids = ids.filter(
        (key) => !available.some((row) => row.id === key && groupOf(row) === 'muscle'),
      )
    if (id) ids.push(id)
    update(ids)
  }
  const choosePackage = (id: string) => {
    setCashSelection((current) => (current === id ? '' : id))
    setChanged(true)
    setEditError('')
  }
  const apply = async (continueToCollection: boolean) => {
    if (changed) {
      if (chosen.some(retired)) {
        setEditError('Remove retired procedure codes before updating this plan.')
        return
      }
      if (!chosen.length) {
        setEditError('Select at least one service.')
        return
      }
      if (
        chosen.some(
          (row) =>
            !Number.isInteger(quantities[row.id!]) ||
            quantities[row.id!] < 1 ||
            quantities[row.id!] > 10000,
        )
      ) {
        setEditError('Choose the planned quantity using the + and − buttons.')
        return
      }
      if (cashSelection && !chosen.some((row) => groupOf(row) === 'nerve')) {
        setEditError('Choose the planned nerve study before applying a cash package.')
        return
      }
      if (
        chosen
          .filter((row) => ['95885', '95886'].includes(row.code))
          .reduce((sum, row) => sum + quantities[row.id!], 0) > 4
      ) {
        setEditError('EMG examinations can include at most four limbs in this scenario.')
        return
      }
      if (busy || !result.check_id || !result.context_token) {
        setEditError('Wait for the current check before updating services.')
        return
      }
      const saved = await save({
        selected_ids: selected,
        confirmed_units: Object.fromEntries(selected.map((id) => [id, quantities[id]])),
        ...(botoxVisit ? { botox } : {}),
        ...(result.cash_schedule ? { cash_selection: cashSelection } : {}),
      })
      if (!saved) return
    }
    if (continueToCollection) viewEstimate()
  }
  useEffect(() => {
    autoSave.current = apply
  })
  useEffect(() => {
    if (!changed || busy) return
    const version = JSON.stringify({ selected, quantities, botox, cashSelection })
    if (lastAutoAttempt.current === version) return
    const timer = window.setTimeout(() => {
      lastAutoAttempt.current = version
      void autoSave.current(false)
    }, 500)
    return () => window.clearTimeout(timer)
  }, [changed, busy, selected, quantities, botox, cashSelection])
  const groups = [
    { id: 'visit', label: 'Office visit', rows: office, single: true },
    { id: 'nerve', label: 'Nerve study', rows: nerve, single: true },
    { id: 'muscle', label: 'Muscle EMG', rows: muscle, single: false },
    {
      id: 'other',
      label: botoxVisit ? 'Injection and medication' : 'Other care',
      rows: other,
      single: false,
    },
  ].filter((group) => group.rows.length)
  const activeGroup = groups.find((group) => group.id === category) || groups[0]
  const quantity = (row: VisitCode, change: number) => {
    setQuantities((current) => ({
      ...current,
      [row.id!]: Math.min(
        groupOf(row) === 'muscle' ? 4 : 10000,
        Math.max(1, (current[row.id!] || 0) + change),
      ),
    }))
    setChanged(true)
    setCashSelection('')
    setEditError('')
  }
  return (
    <section className="visit-planner" aria-labelledby="service-plan-title">
      <h3 id="service-plan-title">Choose care for this estimate</h3>
      <p className="visit-muted">
        {emgVisit && !chosen.length
          ? 'Nerve testing is planned from this booking. Choose the study-count CPT when its scope is documented; final coding follows the care provided.'
          : 'The suggested services are included in the estimate. Remove or change them if planned care differs. Final coding follows the care provided.'}
      </p>
      <div className="selected-service-chips" aria-label="Selected services">
        {chosen.map((row) => {
          const referral = requirementTag(result, row, 'referral')
          const authorization = requirementTag(result, row, 'authorization')
          return (
            <span key={row.id}>
              <Check size={14} aria-hidden="true" />
              <strong>
                {row.code} × {quantities[row.id!] || '—'}
              </strong>
              {result.payment_mode !== 'cash' && (
                <>
                  <small className={`service-requirement-tag is-${referral.tone}`}>
                    {referral.label}
                  </small>
                  <small className={`service-requirement-tag is-${authorization.tone}`}>
                    {authorization.label}
                  </small>
                </>
              )}
            </span>
          )
        })}
        {!chosen.length && (
          <span>
            {result.provisional_scenario?.kind === 'ncs_study_count'
              ? 'One NCS scenario · study count pending · CPT levels are alternatives'
              : result.price_range?.mode === 'alternatives'
                ? 'One office consultation · E/M level pending'
                : 'No services selected'}
          </span>
        )}
      </div>
      {scenarioAlternatives.length > 1 && !chosen.length && result.payment_mode !== 'cash' && (
        <p className="visit-muted">
          Referral: {scenarioRule('referral')}. Prior authorization: {scenarioRule('authorization')}
          . Choose a CPT level to see its own rule and update the estimate.
        </p>
      )}
      <div className="service-category-buttons" aria-label="Service categories">
        {groups.map((group) => (
          <button
            key={group.id}
            aria-pressed={activeGroup?.id === group.id}
            onClick={() => setCategory(group.id)}
          >
            {group.label}
          </button>
        ))}
      </div>
      {activeGroup && (
        <>
          <div className="procedure-group-tools">
            <h3>{activeGroup.label}</h3>
            {activeGroup.single && (
              <button
                disabled={busy || !activeGroup.rows.some((row) => selected.includes(row.id!))}
                onClick={() => pickOne(activeGroup.rows, '')}
              >
                Remove from plan
              </button>
            )}
          </div>
          <div className="procedure-options">
            {activeGroup.rows.map((row) => {
              const included = selected.includes(row.id!),
                single = activeGroup.single
              const disable =
                busy ||
                (groupOf(row) === 'muscle' && !nerve.some((item) => selected.includes(item.id!)))
              const needsCodeSelection =
                !included &&
                result.price_range?.mode === 'alternatives' &&
                ['visit', 'nerve'].includes(groupOf(row))
              const requirement = requirementForLine(
                [
                  ...(result.requirement_evidence?.procedures || []),
                  ...(result.candidate_requirements || []),
                ],
                row.id,
                row.code,
              )
              return (
                <article
                  key={row.id}
                  className={`procedure-choice${included ? ' is-selected' : ''}`}
                >
                  <button
                    className="procedure-pick"
                    aria-pressed={included}
                    disabled={disable}
                    onClick={() =>
                      single
                        ? pickOne(activeGroup.rows, row.id!)
                        : update(
                            included
                              ? selected.filter((id) => id !== row.id)
                              : [...selected, row.id!],
                          )
                    }
                  >
                    <span className="procedure-check" aria-hidden="true">
                      {included && <Check size={16} />}
                    </span>
                    <span>
                      <strong>{clinicalName(row)}</strong>
                      <small>
                        {row.code.startsWith('J') ? 'HCPCS' : 'CPT'} {row.code}
                      </small>
                    </span>
                  </button>
                  <div className="procedure-price-note">
                    {row.unit_allowed_cents != null &&
                    (result.benefits?.network_status !== 'out_of_network' ||
                      ['payer_out_of_network', 'cms_original_medicare_estimate'].includes(
                        row.allowed_basis || '',
                      ))
                      ? `${row.allowed_basis === 'cms_original_medicare_estimate' ? 'CMS benchmark' : row.allowed_basis === 'payer_out_of_network' ? 'Payer allowed' : 'Contract'} / unit ${money(row.unit_allowed_cents)}`
                      : row.unit_cash_cents != null
                        ? `Cash / unit ${money(row.unit_cash_cents)}`
                        : 'Price follows plan and clinic schedule'}
                  </div>
                  {result.payment_mode !== 'cash' &&
                    (included || result.price_range?.mode !== 'alternatives') && (
                      <div className="procedure-requirements">
                        <div>
                          Referral:{' '}
                          <RequirementDecision
                            evidence={requirement?.referral}
                            code={row.code}
                            kind="referral"
                            payerName={result.payer}
                            needsCodeSelection={needsCodeSelection}
                          />
                        </div>
                        <div>
                          Prior auth:{' '}
                          <RequirementDecision
                            evidence={requirement?.authorization}
                            code={row.code}
                            kind="prior authorization"
                            payerName={result.payer}
                            needsCodeSelection={needsCodeSelection}
                          />
                        </div>
                      </div>
                    )}
                  {included && !single && (
                    <div className="procedure-quantity">
                      <span>
                        {groupOf(row) === 'muscle'
                          ? 'Limbs'
                          : row.code.startsWith('J')
                            ? 'Medication units'
                            : 'Quantity'}
                      </span>
                      <div className="quantity-stepper">
                        {row.code.startsWith('J') && (
                          <button
                            aria-label={`Decrease ${row.code} by 10 units`}
                            disabled={busy || quantities[row.id!] <= 1}
                            onClick={() => quantity(row, -10)}
                          >
                            −10
                          </button>
                        )}
                        <button
                          aria-label={`Decrease ${row.code} quantity`}
                          disabled={busy || quantities[row.id!] <= 1}
                          onClick={() => quantity(row, -1)}
                        >
                          <Minus size={16} />
                        </button>
                        <output aria-label={`${row.code} quantity`} aria-live="polite">
                          {quantities[row.id!] || '—'}
                        </output>
                        <button
                          aria-label={`Increase ${row.code} quantity`}
                          disabled={
                            busy || quantities[row.id!] >= (groupOf(row) === 'muscle' ? 4 : 10000)
                          }
                          onClick={() => quantity(row, 1)}
                        >
                          <Plus size={16} />
                        </button>
                        {row.code.startsWith('J') && (
                          <button
                            aria-label={`Increase ${row.code} by 10 units`}
                            disabled={busy || quantities[row.id!] >= 10000}
                            onClick={() => quantity(row, 10)}
                          >
                            +10
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </article>
              )
            })}
          </div>
          {activeGroup.id === 'muscle' && !nerve.some((item) => selected.includes(item.id!)) && (
            <p className="visit-muted">Choose a nerve study first to add needle EMG.</p>
          )}
        </>
      )}
      {result.payment_mode === 'cash' &&
        result.cash_schedule &&
        (emgVisit || chosen.some((row) => groupOf(row) === 'nerve')) && (
          <section className="cash-package-options">
            <h3>Cash package</h3>
            <p className="visit-muted">
              Choose the package that matches the planned care. The focused visit is included. Final
              CPTs follow the services performed.
            </p>
            {result.cash_schedule.packages.map((row) => (
              <button
                key={row.id}
                disabled={busy}
                aria-pressed={cashSelection === row.id}
                onClick={() => choosePackage(row.id)}
              >
                {row.label} · {money(row.amount_cents)}
                {row.indication && <small>{row.indication}</small>}
              </button>
            ))}
          </section>
        )}
      {botoxVisit && (
        <BotoxClinicalReview
          value={botox}
          onChange={(next) => {
            setBotox(next)
            setChanged(true)
            setEditError('')
          }}
          drugSelected={chosen.some((row) => row.code === 'J0585')}
          officeSelected={chosen.some((row) => groupOf(row) === 'visit')}
        />
      )}
      {editError && (
        <p className="visit-error" role="alert">
          {editError}
        </p>
      )}
      <div className="visit-plan-footer">
        <span>
          {chosen.length} {chosen.length === 1 ? 'service' : 'services'}
          {changed ? ' · Saving and recalculating…' : ' · Current estimate'}
        </span>
        <button className="visit-primary" disabled={busy} onClick={() => void apply(true)}>
          {busy ? 'Updating estimate…' : 'Recalculate estimate'} <ArrowRight size={17} />
        </button>
      </div>
    </section>
  )
}
