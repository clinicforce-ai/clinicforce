import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Download,
  FileHeart,
  HeartPulse,
  Hospital,
  ScanLine,
  ShieldCheck,
} from 'lucide-react'
import { animate } from 'animejs'
import { useEffect, useRef, useState } from 'react'
import { enableInsuranceBeta } from '../../services/api/insurance-v1'
import { type DoctorSummary } from '../../services/api/clinical-models'
import { CareEvents, ClinicalItemList, MeasureTable, MedicationTable } from './SummarySections'
import { TrendCharts } from './TrendCharts'

interface DashboardProps {
  data: DoctorSummary
  onScanAnother: () => void
  onHome: () => void
  onVerifyInsurance?: (version: 'v1' | 'v2') => void
}
type SummaryTab = 'overview' | 'care' | 'measurements'

export default function ClinicalSummary({
  data,
  onScanAnother,
  onHome,
  onVerifyInsurance,
}: DashboardProps) {
  const [summaryTab, setSummaryTab] = useState<SummaryTab>('overview')
  const summaryPanel = useRef<HTMLDivElement>(null)
  const [betaMenu, setBetaMenu] = useState<{ x: number; y: number } | null>(null)
  const [betaStatus, setBetaStatus] = useState('')
  useEffect(() => {
    const panel = summaryPanel.current
    if (!panel || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const animation = animate(panel, {
      opacity: [0, 1],
      translateY: [10, 0],
      duration: 240,
      ease: 'outQuad',
    })
    return () => {
      animation.cancel()
    }
  }, [summaryTab])
  useEffect(() => {
    if (!betaMenu) return
    const close = () => setBetaMenu(null)
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', escape)
    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', escape)
    }
  }, [betaMenu])
  const download = () => {
    const link = document.createElement('a')
    link.href = data.pdf_url
    link.download = 'clinicforce-clinical-summary.pdf'
    link.click()
  }
  const openBetaMenu = (x: number, y: number) => {
    if (!onVerifyInsurance) return
    setBetaStatus('')
    setBetaMenu({
      x: Math.min(x, window.innerWidth - 180),
      y: Math.min(y, window.innerHeight - 120),
    })
  }
  const activateBeta = async (version: 'v1' | 'v2') => {
    setBetaMenu(null)
    setBetaStatus(`Enabling insurance beta ${version === 'v2' ? 'V2' : 'V1'}…`)
    try {
      await enableInsuranceBeta()
      setBetaStatus(`Insurance beta ${version === 'v2' ? 'V2' : 'V1'} enabled.`)
      onVerifyInsurance?.(version)
    } catch (error) {
      setBetaStatus(error instanceof Error ? error.message : 'Insurance beta is unavailable.')
    }
  }
  const { summary } = data
  const itemCount =
    summary.vitals.length +
    summary.labs.length +
    summary.medications.length +
    summary.conditions.length +
    summary.allergies.length +
    summary.care_events.length
  const sourcedCount = [
    summary.vitals,
    summary.labs,
    summary.medications,
    summary.conditions,
    summary.allergies,
    summary.care_events,
  ]
    .flat()
    .filter((item) => item.citation).length
  return (
    <section className="summary-page animate-enter" aria-labelledby="summary-heading">
      <button className="back-control" type="button" onClick={onHome}>
        <ArrowLeft aria-hidden="true" className="size-4" /> Back to home
      </button>
      <header className="summary-hero">
        <div>
          <p className="eyebrow">
            <FileHeart aria-hidden="true" className="size-4" /> Clinician view · shared record
          </p>
          <h1 id="summary-heading">Clinical summary</h1>
          <p>
            {itemCount} shared health item{itemCount === 1 ? '' : 's'} · {sourcedCount} linked to
            original report pages.
          </p>
        </div>
        <div className="summary-actions">
          {onVerifyInsurance && (
            <button
              className="button button-secondary insurance-beta-trigger"
              type="button"
              aria-haspopup="menu"
              aria-expanded={!!betaMenu}
              onClick={(event) => {
                const box = event.currentTarget.getBoundingClientRect()
                openBetaMenu(box.left, box.bottom)
              }}
              onContextMenu={(event) => {
                event.preventDefault()
                openBetaMenu(event.clientX, event.clientY)
              }}
              onKeyDown={(event) => {
                if ((event.shiftKey && event.key === 'F10') || event.key === 'ContextMenu') {
                  event.preventDefault()
                  const box = event.currentTarget.getBoundingClientRect()
                  openBetaMenu(box.left, box.bottom)
                }
              }}
            >
              <ShieldCheck aria-hidden="true" className="size-4" /> Verify insurance
            </button>
          )}
          <button className="button button-secondary" type="button" onClick={onScanAnother}>
            <ScanLine aria-hidden="true" className="size-4" /> Scan another
          </button>
          <button className="button button-primary" type="button" onClick={download}>
            <Download aria-hidden="true" className="size-4" /> Download cited PDF
          </button>
        </div>
      </header>
      <p className="sr-status" aria-live="polite">
        {betaStatus}
      </p>
      {betaMenu && (
        <div
          className="beta-context-menu"
          role="menu"
          style={{ left: betaMenu.x, top: betaMenu.y }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <button role="menuitem" type="button" autoFocus onClick={() => void activateBeta('v1')}>
            <ShieldCheck aria-hidden="true" /> Use upload beta
          </button>
          <button role="menuitem" type="button" onClick={() => void activateBeta('v2')}>
            <ShieldCheck aria-hidden="true" /> Use Availity beta V2
          </button>
        </div>
      )}
      <div className="source-assurance">
        <ShieldCheck aria-hidden="true" />
        <span>
          Each linked source label downloads the original private PDF for that fact. Patient-entered
          items are marked separately.
        </span>
      </div>
      <nav className="summary-tabs" aria-label="Clinical summary sections">
        <button
          className={summaryTab === 'overview' ? 'active' : ''}
          aria-pressed={summaryTab === 'overview'}
          type="button"
          onClick={() => setSummaryTab('overview')}
        >
          <HeartPulse aria-hidden="true" /> Overview
        </button>
        <button
          className={summaryTab === 'care' ? 'active' : ''}
          aria-pressed={summaryTab === 'care'}
          type="button"
          onClick={() => setSummaryTab('care')}
        >
          <Hospital aria-hidden="true" /> Care & conditions
        </button>
        <button
          className={summaryTab === 'measurements' ? 'active' : ''}
          aria-pressed={summaryTab === 'measurements'}
          type="button"
          onClick={() => setSummaryTab('measurements')}
        >
          <Activity aria-hidden="true" /> Measurements
        </button>
      </nav>
      <p className="workspace-tabs-helper">
        <strong>Choose a section:</strong> only the information needed for the current review is
        shown, with original sources linked in each record.
      </p>
      {summaryTab === 'overview' && (
        <div className="summary-tab-panel" ref={summaryPanel}>
          <article className="clinical-narrative">
            <div className="narrative-mark">
              <HeartPulse aria-hidden="true" />
            </div>
            <div>
              <p className="section-kicker">Clinical overview</p>
              <h2>Patient history at a glance</h2>
              <p>{summary.narrative}</p>
            </div>
          </article>
          <TrendCharts rows={[...summary.vitals, ...summary.labs]} />
        </div>
      )}
      {summaryTab === 'care' && (
        <div className="summary-grid summary-tab-panel" ref={summaryPanel}>
          <CareEvents rows={summary.care_events} />
          <ClinicalItemList title="Conditions" rows={summary.conditions} icon={Activity} />
          <ClinicalItemList title="Allergies" rows={summary.allergies} icon={AlertTriangle} />
        </div>
      )}
      {summaryTab === 'measurements' && (
        <div className="summary-grid summary-tab-panel" ref={summaryPanel}>
          <MeasureTable
            title="Vitals"
            testLabel="Measurement"
            rows={summary.vitals}
            icon={HeartPulse}
          />
          <MeasureTable title="Labs" testLabel="Test" rows={summary.labs} icon={FileHeart} />
          <MedicationTable rows={summary.medications} />
        </div>
      )}
    </section>
  )
}
