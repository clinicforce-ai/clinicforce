import { lazy, Suspense, useEffect, useState, type FormEvent } from 'react'
import { decryptAndSummarize, type DoctorSummary } from '../../services/api'
import { request, setStaffIntakeContext } from '../../services/api/client'
import { isClinicforceIssuedLink, manifestIdFromUrl, type SmartHealthLink } from '../../services/shl'
import { AccessPin } from './AccessPin'
import { FeatureFallback } from './FeatureFallback'
import { Landing } from './Landing'
import { LoadingSummary } from './LoadingSummary'
import { SiteHeader } from './SiteHeader'
import type { View } from './types'

const Dashboard = lazy(() => import('../clinical-summary'))
const PatientPortal = lazy(() => import('../../components/PatientPortal'))
const Scanner = lazy(() => import('../../components/Scanner'))
const ExternalShlReceiver = lazy(() => import('../../components/ExternalShlReceiver'))
const InsuranceVerification = lazy(() => import('../../components/InsuranceVerification'))
const InsuranceVerificationV2 = lazy(() => import('../../components/InsuranceVerificationV2'))
const StaffWorkspace = lazy(() => import('../../components/StaffWorkspace'))

export function AppShell() {
  const [view, setView] = useState<View>(() => {
    const query = new URLSearchParams(window.location.search)
    return query.has('staff') ? 'staff' : query.has('patient') ? 'patient' : 'landing'
  })
  const [link, setLink] = useState<SmartHealthLink | null>(null)
  const [pin, setPin] = useState('')
  const [summary, setSummary] = useState<DoctorSummary | null>(null)
  const [error, setError] = useState('')
  const [staffIntakeId, setStaffIntakeId] = useState<string | undefined>()
  const [staffQr, setStaffQr] = useState(false)
  useEffect(() => { if (view === 'staff') window.history.replaceState({}, '', '?staff') }, [view])
  const restart = () => { setView('scan'); setLink(null); setPin(''); setSummary(null); setError('') }
  const returnHome = () => { setStaffIntakeContext(null); setStaffIntakeId(undefined); setStaffQr(false); setView('landing'); setLink(null); setPin(''); setSummary(null); setError(''); window.history.replaceState({}, '', window.location.pathname) }
  const returnStaff = () => { setStaffIntakeContext(null); setStaffQr(false); setView('staff') }
  const handleLinkScanned = (nextLink: SmartHealthLink) => {
    setLink(nextLink); setError(''); setView(isClinicforceIssuedLink(nextLink) ? 'pin' : 'external')
  }
  const handleSubmitPin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!link || !/^\d{4}$/.test(pin)) { setError('Enter the four-digit PIN supplied by the patient.'); return }
    setError(''); setView('loading')
    try {
      setSummary(await decryptAndSummarize(manifestIdFromUrl(link.manifestUrl), link.key, pin))
      if (staffQr) {
        const intake = await request<{ id: string }>('/api/doctor/insurance/staff-intake', { method: 'POST' })
        setStaffIntakeId(intake.id); returnStaff()
      } else setView('dashboard')
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to retrieve the patient file.'); setView('pin') }
  }
  const openStaffInsurance = (id: string, version: 'v1' | 'v2') => {
    setStaffIntakeId(id); setStaffIntakeContext(id); setView(version === 'v2' ? 'insurance-v2' : 'insurance')
  }
  return <div className="min-h-dvh bg-[var(--canvas)] text-[var(--ink)]">
    <a className="skip-link" href="#main-content">Skip to content</a><SiteHeader onHome={returnHome} staffMode={view === 'staff' || !!staffIntakeId || staffQr} />
    <main id="main-content" className="shell py-8 sm:py-12">
      {view === 'landing' && <Landing onChoosePatient={() => setView('patient')} onChooseClinician={() => setView('scan')} onChooseStaff={() => { setStaffIntakeId(undefined); setView('staff') }} />}
      <Suspense fallback={<FeatureFallback />}>
        {view === 'patient' && <PatientPortal onBack={returnHome} />}
        {view === 'staff' && <StaffWorkspace onBack={returnHome} initialIntakeId={staffIntakeId} onOpenInsurance={openStaffInsurance} onScanQr={() => { setStaffQr(true); setStaffIntakeContext(null); restart() }} />}
        {view === 'scan' && <Scanner clinicforceOnly={staffQr} onScanned={handleLinkScanned} onCancel={staffQr ? returnStaff : returnHome} />}
        {view === 'external' && link && <ExternalShlReceiver link={link} onCancel={restart} onImported={() => { setLink(null); setView('patient') }} />}
        {view === 'pin' && <AccessPin pin={pin} error={error} onBack={restart} onPinChange={setPin} onSubmit={handleSubmitPin} />}
        {view === 'loading' && <LoadingSummary />}
        {view === 'dashboard' && summary && <Dashboard data={summary} onScanAnother={restart} onHome={returnHome} onVerifyInsurance={(version) => { setStaffIntakeContext(null); setStaffIntakeId(undefined); setView(version === 'v2' ? 'insurance-v2' : 'insurance') }} />}
        {view === 'insurance' && <InsuranceVerification staffMode={!!staffIntakeId} onBack={staffIntakeId ? returnStaff : () => setView(summary ? 'dashboard' : 'landing')} />}
        {view === 'insurance-v2' && <InsuranceVerificationV2 staffMode={!!staffIntakeId} onUseDocuments={staffIntakeId ? () => openStaffInsurance(staffIntakeId, 'v1') : undefined} onBack={staffIntakeId ? returnStaff : () => setView(summary ? 'dashboard' : 'landing')} />}
      </Suspense>
    </main><footer className="shell border-t border-[var(--line)] py-6 text-sm text-[var(--muted-ink)]">ClinicForce · Patient records and clinic insurance workspace</footer>
  </div>
}
