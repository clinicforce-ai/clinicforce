import { useEffect, useState } from 'react'
import { request } from '../../services/api/client'
import {
  createInsuranceCase,
  runInsuranceStage,
  uploadInsuranceEvidence,
} from '../../services/api/insurance-v1'
import { type InsuranceCase, type InsuranceStage } from '../../services/api/insurance'
import { label } from './v1-display'

interface Props {
  onBack: () => void
  staffMode?: boolean
  staffIntakeId?: string
}
export function useInsuranceV1Model({ onBack, staffMode = false, staffIntakeId }: Props) {
  const [insuranceCase, setInsuranceCase] = useState<InsuranceCase | null>(null)
  const [viewStage, setViewStage] = useState<InsuranceStage | null>(null)
  const [selectedCodes, setSelectedCodes] = useState<string[]>([])
  const [quantities, setQuantities] = useState<Record<string, number>>({})
  const [contextEdited, setContextEdited] = useState(false)
  const [suggestionsStale, setSuggestionsStale] = useState(false)
  const [clinicalContext, setClinicalContext] = useState('')
  const [visitType, setVisitType] = useState<'' | 'new' | 'established'>('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('Start with the patient’s insurance card.')
  const [error, setError] = useState('')

  const perform = async (
    work: () => Promise<InsuranceCase>,
    success: string,
    after?: (next: InsuranceCase) => void,
  ) => {
    setBusy(true)
    setError('')
    try {
      const next = await work()
      setInsuranceCase(next)
      setViewStage(null)
      setSelectedCodes(next.service_lines.filter((line) => line.selected).map((line) => line.code))
      setMessage(next.status === 'complete' ? 'Pay estimate review complete.' : success)
      after?.(next)
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : 'The uploaded document could not be processed.',
      )
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    if (!staffMode) return
    let canceled = false
    request<InsuranceCase>(
      '/api/doctor/insurance/staff-case',
      { method: 'POST' },
      { staffIntakeId },
    )
      .then((next) => {
        if (canceled) return
        setInsuranceCase(next)
        setSelectedCodes(
          next.service_lines.filter((line) => line.selected).map((line) => line.code),
        )
        setClinicalContext(next.clinical_context ?? '')
        setVisitType(next.visit_type ?? '')
        setQuantities(
          Object.fromEntries(
            next.service_lines.filter((line) => line.units).map((line) => [line.code, line.units!]),
          ),
        )
        setMessage('Saved insurance review opened. Patient details were confirmed by clinic staff.')
      })
      .catch((caught: unknown) => {
        if (!canceled)
          setError(caught instanceof Error ? caught.message : 'Unable to open the saved review.')
      })
    return () => {
      canceled = true
    }
  }, [staffMode, staffIntakeId])
  const uploadCard = (file?: File) => {
    if (!file) return
    if (file.size > 10 * 1024 * 1024) {
      setError('Choose a card file of 10 MB or less.')
      return
    }
    setMessage('Reading your insurance card…')
    void perform(
      () => createInsuranceCase(file, staffIntakeId),
      'Card read. Compare the extracted fields with your uploaded card.',
      () => {
        setClinicalContext('')
        setVisitType('')
        setContextEdited(false)
        setSuggestionsStale(false)
        setQuantities({})
      },
    )
  }
  const uploadEvidence = (kind: string, file?: File) => {
    if (!insuranceCase || !file) return
    if (file.size > 15 * 1024 * 1024) {
      setError('Choose a document of 15 MB or less.')
      return
    }
    setMessage(`Reading ${file.name}…`)
    void perform(
      () => uploadInsuranceEvidence(insuranceCase.id, kind, file, staffIntakeId),
      `${file.name} added to this review.`,
      (next) => {
        if (kind === 'clinical') {
          if (!contextEdited) {
            setClinicalContext(next.clinical_context ?? '')
            setVisitType(next.visit_type ?? '')
          }
          setQuantities({})
          setSuggestionsStale(false)
          setMessage(
            contextEdited
              ? 'Clinical document read. Your edited text was kept; review the extracted passages below.'
              : 'Clinical document read. Review the extracted visit details before requesting codes.',
          )
        }
      },
    )
  }
  const advance = (stage: Exclude<InsuranceStage, 'intake' | 'service'>) =>
    insuranceCase &&
    void perform(
      () => runInsuranceStage(insuranceCase.id, stage, staffIntakeId),
      'Ready for the next step.',
    )
  const activeStage =
    insuranceCase?.status === 'complete' ? 'estimate' : (insuranceCase?.next_stage ?? 'intake')
  const stage = viewStage ?? activeStage
  const reviewing = stage !== activeStage
  const disabled = busy || reviewing
  const facts = insuranceCase?.facts ?? []
  const documents = insuranceCase?.documents ?? {}
  const estimate = insuranceCase?.estimate
  const groups = insuranceCase?.service_groups ?? []
  const visitCodes = groups
    .filter((group) => group.category === 'visit')
    .flatMap((group) => group.codes)
  const activeGroups = new Set(
    groups
      .filter((group) => group.codes.some((code) => selectedCodes.includes(code)))
      .map((group) => group.id),
  )
  const missingCompanions = groups
    .filter((group) => activeGroups.has(group.id))
    .flatMap((group) => group.requires_groups.filter((id) => !activeGroups.has(id)))
    .map((id) => groups.find((group) => group.id === id)?.label ?? id)
  const missingQuantity = insuranceCase?.service_lines.some(
    (line) =>
      selectedCodes.includes(line.code) &&
      line.requires_quantity &&
      (!Number.isInteger(quantities[line.code]) ||
        quantities[line.code] < 1 ||
        quantities[line.code] > 10000),
  )
  const completed = new Set(insuranceCase?.stage_decisions.map((item) => item.stage) ?? [])
  if (insuranceCase?.ocr.confirmed) completed.add('intake')
  const cardValues = Object.entries(insuranceCase?.ocr_evidence ?? {}).filter(
    ([key, evidence]) =>
      evidence.value === insuranceCase?.ocr[key as keyof InsuranceCase['ocr']] &&
      evidence.quote.includes(evidence.value),
  )

  const cardReview = insuranceCase && (
    <>
      {staffMode && (
        <p className="feedback feedback-success">
          Patient and insurance identity were confirmed by clinic staff. Original cards and
          corrections are saved in the patient insurance workspace.
        </p>
      )}
      <dl className="insurance-facts card-details">
        {cardValues.map(([key, evidence]) => (
          <div key={key}>
            <dt>{label(key)}</dt>
            <dd>
              {evidence.value}
              <small>
                Source: {evidence.source_filename || insuranceCase.card_source?.filename} · page{' '}
                {evidence.source_page ?? evidence.page}
              </small>
              <details className="source-excerpt">
                <summary>View extracted text</summary>
                <blockquote>{evidence.quote}</blockquote>
              </details>
            </dd>
          </div>
        ))}
      </dl>
      {['member_name', 'member_id', 'group_number']
        .filter((key) => !cardValues.some(([found]) => found === key))
        .map((key) => (
          <p className="missing-source" key={key}>
            {label(key)}: not clearly found in this upload.
          </p>
        ))}
      <p className="missing-source">
        Only fields found in this card are shown. Benefits that are not printed on the card belong
        in the benefits upload—not in this card summary.
      </p>
    </>
  )
  return {
    onBack,
    staffMode,
    staffIntakeId,
    insuranceCase,
    setInsuranceCase,
    viewStage,
    setViewStage,
    selectedCodes,
    setSelectedCodes,
    quantities,
    setQuantities,
    contextEdited,
    setContextEdited,
    suggestionsStale,
    setSuggestionsStale,
    clinicalContext,
    setClinicalContext,
    visitType,
    setVisitType,
    busy,
    message,
    error,
    perform,
    uploadCard,
    uploadEvidence,
    advance,
    activeStage,
    stage,
    reviewing,
    disabled,
    facts,
    documents,
    estimate,
    groups,
    visitCodes,
    activeGroups,
    missingCompanions,
    missingQuantity,
    completed,
    cardValues,
    cardReview,
  }
}
export type InsuranceV1Model = ReturnType<typeof useInsuranceV1Model>
