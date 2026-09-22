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
const CommercialEstimator = lazy(() => import('../../components/CommercialEstimator'))
const StaffWorkspace = lazy(() => import('../../components/StaffWorkspace'))
const PatientInsuranceUpload = lazy(() => import('../../components/PatientInsuranceUpload'))
const StaffOnboarding = lazy(() => import('../../components/StaffOnboarding'))

function entryRoute() {
  const query = new URLSearchParams(window.location.search)
  const parts = window.location.pathname.split('/').filter(Boolean)
  const invitation = query.get('join') || (parts[0] === 'join' ? parts[1] : '') || ''
  const clinic = query.get('clinic') || (/^[a-z][a-z0-9-]{2,39}$/.test(parts[0] || '') && !['api', 'assets', 'join', 'upload', 'staff', 'patient', 'manifest'].includes(parts[0]) && parts.length <= 2 ? parts[0] : '') || ''
  const upload = query.get('upload') || (clinic && parts.length === 2 ? parts[1] : '') || ''
  const view: View = query.get('staff') === 'create' ? 'staff-create' : query.has('join') || invitation || parts[0] === 'join' ? 'staff-join' : query.has('staff') || parts[0] === 'staff' ? 'staff' : query.has('patient') || parts[0] === 'patient' ? 'patient' : clinic || parts[0] === 'upload' || query.has('upload') ? 'patient-upload' : 'landing'
  return { view, clinic, upload, invitation }
}

export function AppShell() {
  const [entry, setEntry] = useState(entryRoute)
  const [view, setView] = useState<View>(entry.view)
  const [link, setLink] = useState<SmartHealthLink | null>(null)
  const [pin, setPin] = useState('')
  const [summary, setSummary] = useState<DoctorSummary | null>(null)
  const [error, setError] = useState('')
  const [staffIntakeId, setStaffIntakeId] = useState<string | undefined>()
  const [staffQr, setStaffQr] = useState(false)
  useEffect(() => { if (view === 'staff') window.history.replaceState({}, '', '/?staff') }, [view])
  useEffect(() => { const pop = () => { const route = entryRoute(); setEntry(route); setView(route.view); setStaffIntakeContext(null); setStaffIntakeId(undefined); setStaffQr(false) }; window.addEventListener('popstate', pop); return () => window.removeEventListener('popstate', pop) }, [])
  const openEntry = (next: View, url: string) => { window.history.pushState({}, '', url); setEntry(entryRoute()); setView(next) }
  const restart = () => { setView('scan'); setLink(null); setPin(''); setSummary(null); setError('') }
  const returnHome = () => { setStaffIntakeContext(null); setStaffIntakeId(undefined); setStaffQr(false); setView('landing'); setLink(null); setPin(''); setSummary(null); setError(''); window.history.replaceState({}, '', '/') }
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
  const openStaffInsurance = (id: string, version: 'v1' | 'v2' | 'commercial') => {
    setStaffIntakeId(id); setStaffIntakeContext(id); setView(version === 'v2' ? 'insurance-v2' : version === 'commercial' ? 'commercial-estimator' : 'insurance')
  }
  return <div className="min-h-dvh bg-[var(--canvas)] text-[var(--ink)]">
    <a className="skip-link" href="#main-content">Skip to content</a><SiteHeader onHome={returnHome} insuranceUploadMode={view === 'patient-upload'} staffMode={view === 'staff' || view === 'staff-create' || view === 'staff-join' || !!staffIntakeId || staffQr} />
    <main id="main-content" className="shell py-8 sm:py-12">
      {view === 'landing' && <Landing onChoosePatient={() => openEntry('patient', '/?patient')} onChooseUpload={() => openEntry('patient-upload', '/?upload')} onChooseStaff={() => { setStaffIntakeId(undefined); openEntry('staff', '/?staff') }} />}
      <Suspense fallback={<FeatureFallback />}>
        {view === 'patient' && <PatientPortal onBack={returnHome} />}
        {view === 'patient-upload' && <PatientInsuranceUpload key={`${entry.clinic}/${entry.upload}`} clinicCode={entry.clinic} uploadCode={entry.upload} onBack={returnHome} />}
        {(view === 'staff-create' || view === 'staff-join') && <StaffOnboarding key={`${view}/${entry.invitation}`} mode={view === 'staff-create' ? 'create' : 'join'} token={entry.invitation} onBack={returnStaff} onComplete={returnStaff} onJoin={token => openEntry('staff-join', `/?join=${encodeURIComponent(token)}`)} />}
        {view === 'staff' && <StaffWorkspace onBack={returnHome} onCreateClinic={() => openEntry('staff-create', '/?staff=create')} onJoinClinic={() => openEntry('staff-join', '/?join')} onOpenPatientQr={() => { setStaffQr(false); restart() }} initialIntakeId={staffIntakeId} onOpenInsurance={openStaffInsurance} onScanQr={() => { setStaffQr(true); setStaffIntakeContext(null); restart() }} />}
        {view === 'scan' && <Scanner clinicforceOnly={staffQr} onScanned={handleLinkScanned} onCancel={returnStaff} />}
        {view === 'external' && link && <ExternalShlReceiver link={link} onCancel={restart} onImported={() => { setLink(null); setView('patient') }} />}
        {view === 'pin' && <AccessPin pin={pin} error={error} onBack={restart} onPinChange={setPin} onSubmit={handleSubmitPin} />}
        {view === 'loading' && <LoadingSummary />}
        {view === 'dashboard' && summary && <Dashboard data={summary} onScanAnother={restart} onHome={returnHome} onVerifyInsurance={(version) => { setStaffIntakeContext(null); setStaffIntakeId(undefined); setView(version === 'v2' ? 'insurance-v2' : 'insurance') }} />}
        {view === 'insurance' && <InsuranceVerification staffMode={!!staffIntakeId} onBack={staffIntakeId ? returnStaff : () => setView(summary ? 'dashboard' : 'landing')} />}
        {view === 'insurance-v2' && <InsuranceVerificationV2 staffMode={!!staffIntakeId} onUseDocuments={staffIntakeId ? () => openStaffInsurance(staffIntakeId, 'v1') : undefined} onBack={staffIntakeId ? returnStaff : () => setView(summary ? 'dashboard' : 'landing')} />}
        {view === 'commercial-estimator' && <CommercialEstimator onBack={returnStaff} />}
      </Suspense>
    </main><footer className="shell border-t border-[var(--line)] py-6 text-sm text-[var(--muted-ink)]">ClinicForce · Patient records and clinic insurance workspace</footer>
  </div>
}
