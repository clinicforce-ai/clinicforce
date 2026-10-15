import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  confirmV2Services,
  getV2Configuration,
  getV2Requirements,
  getV2Appointments,
  latestV2Case,
  refreshV2Transaction,
  submitV2Transaction,
  saveV2ManualReview,
  suggestV2Services,
  uploadV2Document,
} from '../../services/api/insurance-v2'
import {
  type V2Appointment,
  type V2Case,
  type V2Configuration,
  type V2ManualReview,
  type V2Resource,
} from '../../services/api/insurance-v2-types'
import { money, resources } from './v2-display'
import type { InsuranceV2Props } from './v2-props'

export type Choice = {
  selected: boolean
  quantity: number
  service_status: 'scheduled' | 'possible'
}
export function useInsuranceV2Model({
  onBack,
  staffMode = false,
  staffIntakeId,
  onUseDocuments,
}: InsuranceV2Props) {
  const [config, setConfig] = useState<V2Configuration | null>(null)
  const [insuranceCase, setCase] = useState<V2Case | null>(null)
  const [appointments, setAppointments] = useState<V2Appointment[]>([])
  const [appointmentId, setAppointmentId] = useState('')
  const [busy, setBusy] = useState('Loading insurance settings…')
  const [error, setError] = useState('')
  const [choices, setChoices] = useState<Record<string, Choice>>({})
  const [copied, setCopied] = useState(false)
  const active = useRef(false)
  const pollStarted = useRef<Record<string, number>>({})
  const autoSuggested = useRef<string>('')

  useEffect(() => {
    let canceled = false
    Promise.all([
      getV2Configuration(staffIntakeId),
      latestV2Case(staffIntakeId),
      staffMode ? getV2Appointments(staffIntakeId) : Promise.resolve({ appointments: [] }),
    ])
      .then(([settings, latest, visits]) => {
        if (!canceled) {
          setConfig(settings)
          setCase(latest.case)
          setAppointments(visits.appointments)
        }
      })
      .catch((caught: unknown) => {
        if (!canceled)
          setError(
            caught instanceof Error ? caught.message : 'Unable to open insurance verification.',
          )
      })
      .finally(() => {
        if (!canceled) setBusy('')
      })
    return () => {
      canceled = true
    }
  }, [staffMode, staffIntakeId])

  const perform = useCallback(async (label: string, operation: () => Promise<V2Case>) => {
    if (active.current) return
    active.current = true
    setBusy(label)
    setError('')
    try {
      setCase(await operation())
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'The request could not be completed. Refresh the saved case before trying again.',
      )
    } finally {
      active.current = false
      setBusy('')
    }
  }, [])

  useEffect(() => {
    if (!insuranceCase || busy || error) return
    const pending = resources.find(
      ({ id }) => insuranceCase.transactions[id]?.outcome === 'pending',
    )
    if (!pending) return
    const key = `${insuranceCase.id}:${pending.id}`
    const started = pollStarted.current[key] ?? Date.now()
    pollStarted.current[key] = started
    if (Date.now() - started >= 60_000) return
    const transaction = insuranceCase.transactions[pending.id]!
    const delay = Math.max(3000, (transaction.next_poll_at ?? 0) * 1000 - Date.now())
    if (Date.now() + delay - started > 60_000) return
    const timer = window.setTimeout(() => {
      void perform('Checking the saved Availity response…', () =>
        refreshV2Transaction(insuranceCase.id, pending.id, staffIntakeId),
      )
    }, delay)
    return () => window.clearTimeout(timer)
  }, [insuranceCase, busy, error, perform, staffIntakeId])

  const stediTemporary = config?.stedi_temporary_enabled === true
  const demo = config?.environment === 'demo' && !stediTemporary
  const ecwPreview = staffMode && demo
  const ecwSource = insuranceCase?.sources.ecw
  const visibleMissing = ecwPreview ? ecwSource?.missing || [] : insuranceCase?.missing || []
  const additionalMissing = visibleMissing.filter(
    (item) =>
      !/^(No CPT\/HCPCS-coded ServiceRequest|No ICD-10 diagnosis is linked|ClinicForce cannot read eClinicalWorks charge items)/.test(
        item,
      ),
  )
  const appointmentDate = insuranceCase?.inputs.service_date || ''
  const upcomingAppointment =
    /^\d{4}-\d{2}-\d{2}$/.test(appointmentDate) &&
    appointmentDate > new Date().toISOString().slice(0, 10)
  const visitStatus =
    insuranceCase?.inputs.visit_type === 'new'
      ? 'New patient'
      : insuranceCase?.inputs.visit_type === 'established'
        ? 'Established patient'
        : ''
  const hasEmSuggestions =
    insuranceCase?.suggestions?.groups.some((group) => group.category === 'visit') || false
  const coverage = insuranceCase?.transactions.coverage
  const coverageDone = coverage?.outcome === 'complete'
  const coverageActive = !ecwPreview && (demo || coverage?.result?.active === true)
  const stediCoverage = coverage?.source === 'stedi' || stediTemporary
  const servicesLocked =
    !!insuranceCase &&
    (['estimate', 'procedure_estimate', 'authorization', 'referral'] as V2Resource[]).some(
      (resource) => !!insuranceCase.transactions[resource],
    )
  const hasPending =
    !!insuranceCase &&
    resources.some((resource) =>
      ['submitting', 'pending'].includes(insuranceCase.transactions[resource.id]?.outcome || ''),
    )

  useEffect(() => {
    if (
      !insuranceCase ||
      busy ||
      error ||
      (!insuranceCase.inputs.reason && !insuranceCase.inputs.lines.length) ||
      insuranceCase.suggestions ||
      servicesLocked
    )
      return
    const key = `${insuranceCase.id}:${insuranceCase.revision}:${insuranceCase.inputs.reason}`
    if (autoSuggested.current === key) return
    autoSuggested.current = key
    void perform('Finding clinician-reviewable services from the eCW appointment…', () =>
      suggestV2Services(insuranceCase.id, staffIntakeId),
    )
  }, [insuranceCase, busy, error, perform, servicesLocked, staffIntakeId])

  const run = (resource: V2Resource) => {
    if (!insuranceCase || ecwPreview) return
    void perform(
      `Sending ${resources.find((item) => item.id === resource)?.label.toLowerCase()} to the payer…`,
      async () => {
        let current = insuranceCase
        if (!demo && !current.discovery.checked_at)
          current = await getV2Requirements(current.id, staffIntakeId)
        return submitV2Transaction(current.id, resource, staffIntakeId)
      },
    )
  }

  const getAnswer = () => {
    if (!insuranceCase || ecwPreview) return
    void perform('Checking the selected appointment with the payer…', async () => {
      let current = insuranceCase
      if (!demo && !current.discovery.checked_at)
        current = await getV2Requirements(current.id, staffIntakeId)
      if (!current.transactions.coverage)
        current = await submitV2Transaction(current.id, 'coverage', staffIntakeId)
      const coverageResult = current.transactions.coverage?.result
      const canEstimate =
        current.transactions.coverage?.outcome === 'complete' &&
        coverageResult?.active === true &&
        coverageResult.pce_available !== false &&
        current.missing_by_resource.estimate.length === 0 &&
        current.inputs.lines.some((line) => (line.service_status || 'scheduled') === 'scheduled')
      return canEstimate && !current.transactions.estimate
        ? submitV2Transaction(current.id, 'estimate', staffIntakeId)
        : current
    })
  }

  const uploadFees = (file: File) => {
    if (!insuranceCase) return
    void perform('Reading clinic prices and suggesting services…', async () => {
      const updated = await uploadV2Document(insuranceCase.id, 'fees', file, staffIntakeId)
      return updated.inputs.reason ? suggestV2Services(updated.id, staffIntakeId) : updated
    })
  }

  const toggle = (code: string, groupCodes: string[], single: boolean) => {
    setChoices((current) => {
      const next = { ...current }
      if (single)
        groupCodes.forEach((item) => {
          if (item !== code) delete next[item]
        })
      next[code] = {
        selected: !current[code]?.selected,
        quantity: current[code]?.quantity || 1,
        service_status: current[code]?.service_status || 'scheduled',
      }
      return next
    })
  }

  const confirmServices = () => {
    if (!insuranceCase) return
    const lines = Object.entries(choices)
      .filter(([, choice]) => choice.selected)
      .map(([code, choice]) => ({
        code,
        quantity: choice.quantity,
        service_status: choice.service_status,
      }))
    if (!lines.length) {
      setError('Choose at least one clinician-reviewed service.')
      return
    }
    void perform('Saving reviewed services…', () =>
      confirmV2Services(insuranceCase.id, lines, staffIntakeId),
    )
  }

  const recordManualReview = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!insuranceCase) return
    const values = new FormData(event.currentTarget)
    const review: V2ManualReview = {
      source: values.get('source') as V2ManualReview['source'],
      network_status: values.get('network_status') as V2ManualReview['network_status'],
      network_reference: String(values.get('network_reference') || ''),
      referral_status: values.get('referral_status') as V2ManualReview['referral_status'],
      referral_reference: String(values.get('referral_reference') || ''),
      authorization_status: values.get(
        'authorization_status',
      ) as V2ManualReview['authorization_status'],
      authorization_reference: String(values.get('authorization_reference') || ''),
      price_path: values.get('price_path') as V2ManualReview['price_path'],
    }
    void perform('Saving manual payer verification…', () =>
      saveV2ManualReview(insuranceCase.id, review, staffIntakeId),
    )
  }

  const copySummary = async () => {
    if (!insuranceCase) return
    const summary = insuranceCase.summary
    const text = [
      summary.heading,
      `Coverage: ${summary.coverage}`,
      `Network: ${summary.scheduled.network || 'not returned'}`,
      `Scheduled insurance estimate: ${money(summary.scheduled.insurance_cents)}`,
      `Scheduled cash price: ${money(summary.scheduled.cash_cents)}`,
      `Referral: ${summary.referral.status || (summary.referral.required ? 'required — no determination' : 'not identified as required')}`,
      `Prior authorization: ${summary.authorization.status || (summary.authorization.required ? 'required — no determination' : 'not identified as required')}`,
    ].join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setError('The browser could not copy the summary. Select the result and copy it manually.')
    }
  }

  return {
    onBack,
    staffMode,
    staffIntakeId,
    onUseDocuments,
    config,
    insuranceCase,
    setCase,
    appointments,
    appointmentId,
    setAppointmentId,
    busy,
    error,
    choices,
    setChoices,
    copied,
    perform,
    stediTemporary,
    demo,
    ecwPreview,
    ecwSource,
    visibleMissing,
    additionalMissing,
    appointmentDate,
    upcomingAppointment,
    visitStatus,
    hasEmSuggestions,
    coverage,
    coverageDone,
    coverageActive,
    stediCoverage,
    servicesLocked,
    hasPending,
    run,
    getAnswer,
    uploadFees,
    toggle,
    confirmServices,
    recordManualReview,
    copySummary,
  }
}
export type InsuranceV2Model = ReturnType<typeof useInsuranceV2Model>
