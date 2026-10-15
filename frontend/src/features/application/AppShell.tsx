import { lazy, Suspense, useEffect, useState, type FormEvent } from 'react'
import { decryptAndSummarize } from '../../services/api/clinician'
import { type DoctorSummary } from '../../services/api/clinical-models'
import { request } from '../../services/api/client'
import {
  isClinicforceIssuedLink,
  manifestIdFromUrl,
  type SmartHealthLink,
} from '../../services/shl'
import { AccessPin } from './AccessPin'
import { FeatureFallback } from './FeatureFallback'
import { Landing } from './Landing'
import { LoadingSummary } from './LoadingSummary'
import { SiteHeader } from './SiteHeader'
import type { View } from './types'
import { parseEntryRoute } from './entry-route'

const Dashboard = lazy(() => import('../clinical-summary'))
const PatientPortal = lazy(() => import('../patient-workspace/PatientPortal'))
const Scanner = lazy(() => import('../sharing/Scanner'))
const ExternalShlReceiver = lazy(() => import('../sharing/ExternalShlReceiver'))
const InsuranceVerification = lazy(() => import('../insurance-v1/InsuranceVerification'))
const InsuranceVerificationV2 = lazy(() => import('../insurance-v2/InsuranceVerificationV2'))
const CommercialEstimator = lazy(() => import('../commercial-estimate/CommercialEstimator'))
const StaffWorkspace = lazy(() => import('../staff-workspace/StaffWorkspace'))
const PatientInsuranceUpload = lazy(() => import('../patient-upload/PatientInsuranceUpload'))
const StaffOnboarding = lazy(() => import('../staff-workspace/StaffOnboarding'))

export function AppShell() {
  const [entry, setEntry] = useState(() => parseEntryRoute(window.location))
  const [view, setView] = useState<View>(entry.view)
  const [link, setLink] = useState<SmartHealthLink | null>(null)
  const [pin, setPin] = useState('')
  const [summary, setSummary] = useState<DoctorSummary | null>(null)
  const [error, setError] = useState('')
  const [staffIntakeId, setStaffIntakeId] = useState<string | undefined>()
  const [resolveEcwPatient, setResolveEcwPatient] = useState(false)
  const [staffQr, setStaffQr] = useState(false)
  const [refreshBenefits, setRefreshBenefits] = useState(false)
  useEffect(() => {
    if (view === 'staff') window.history.replaceState({}, '', '/?staff')
  }, [view])
  useEffect(() => {
    const pop = () => {
      const route = parseEntryRoute(window.location)
      setEntry(route)
      setView(route.view)
      setStaffIntakeId(undefined)
      setStaffQr(false)
    }
    window.addEventListener('popstate', pop)
    return () => window.removeEventListener('popstate', pop)
  }, [])
  const openEntry = (next: View, url: string) => {
    window.history.pushState({}, '', url)
    setEntry(parseEntryRoute(window.location))
    setView(next)
  }
  const restart = () => {
    setView('scan')
    setLink(null)
    setPin('')
    setSummary(null)
    setError('')
  }
  const returnHome = () => {
    setStaffIntakeId(undefined)
    setStaffQr(false)
    setView('landing')
    setLink(null)
    setPin('')
    setSummary(null)
    setError('')
    window.history.replaceState({}, '', '/')
  }
  const returnStaff = () => {
    setResolveEcwPatient(false)
    setStaffQr(false)
    setView('staff')
  }
  const handleLinkScanned = (nextLink: SmartHealthLink) => {
    setLink(nextLink)
    setError('')
    setView(isClinicforceIssuedLink(nextLink) ? 'pin' : 'external')
  }
  const handleSubmitPin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!link || !/^\d{4}$/.test(pin)) {
      setError('Enter the four-digit PIN supplied by the patient.')
      return
    }
    setError('')
    setView('loading')
    try {
      setSummary(await decryptAndSummarize(manifestIdFromUrl(link.manifestUrl), link.key, pin))
      if (staffQr) {
        const intake = await request<{ id: string }>('/api/doctor/insurance/staff-intake', {
          method: 'POST',
        })
        setStaffIntakeId(intake.id)
        returnStaff()
      } else setView('dashboard')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to retrieve the patient file.')
      setView('pin')
    }
  }
  const openStaffInsurance = (id: string, version: 'v1' | 'v2' | 'commercial', refresh = false) => {
    setResolveEcwPatient(false)
    setRefreshBenefits(refresh)
    setStaffIntakeId(id)
    setView(version === 'v1' ? 'insurance' : 'commercial-estimator')
  }
  return (
    <div className="min-h-dvh bg-[var(--canvas)] text-[var(--ink)]">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <SiteHeader
        onHome={returnHome}
        insuranceUploadMode={view === 'patient-upload'}
        staffMode={
          view === 'staff' ||
          view === 'staff-create' ||
          view === 'staff-join' ||
          !!staffIntakeId ||
          staffQr
        }
      />
      <main
        id="main-content"
        className={`shell ${view === 'commercial-estimator' ? 'py-4 sm:py-5' : 'py-8 sm:py-12'}`}
      >
        {view === 'landing' && (
          <Landing
            onChoosePatient={() => openEntry('patient', '/?patient')}
            onChooseUpload={() => openEntry('patient-upload', '/?upload')}
            onChooseStaff={() => {
              setStaffIntakeId(undefined)
              openEntry('staff', '/?staff')
            }}
          />
        )}
        <Suspense fallback={<FeatureFallback />}>
          {view === 'patient' && <PatientPortal onBack={returnHome} />}
          {view === 'patient-upload' && (
            <PatientInsuranceUpload
              key={`${entry.clinic}/${entry.upload}`}
              clinicCode={entry.clinic}
              uploadCode={entry.upload}
              onBack={returnHome}
            />
          )}
          {(view === 'staff-create' || view === 'staff-join') && (
            <StaffOnboarding
              key={`${view}/${entry.invitation}`}
              mode={view === 'staff-create' ? 'create' : 'join'}
              token={entry.invitation}
              onBack={returnStaff}
              onComplete={returnStaff}
              onJoin={(token) => openEntry('staff-join', `/?join=${encodeURIComponent(token)}`)}
            />
          )}
          {view === 'staff' && (
            <StaffWorkspace
              onBack={returnHome}
              onCreateClinic={() => openEntry('staff-create', '/?staff=create')}
              onJoinClinic={() => openEntry('staff-join', '/?join')}
              onOpenPatientQr={() => {
                setStaffQr(false)
                restart()
              }}
              initialIntakeId={staffIntakeId}
              initialEcwLookup={resolveEcwPatient}
              onOpenInsurance={openStaffInsurance}
              onScanQr={() => {
                setStaffQr(true)
                restart()
              }}
            />
          )}
          {view === 'scan' && (
            <Scanner
              clinicforceOnly={staffQr}
              onScanned={handleLinkScanned}
              onCancel={returnStaff}
            />
          )}
          {view === 'external' && link && (
            <ExternalShlReceiver
              link={link}
              onCancel={restart}
              onImported={() => {
                setLink(null)
                setView('patient')
              }}
            />
          )}
          {view === 'pin' && (
            <AccessPin
              pin={pin}
              error={error}
              onBack={restart}
              onPinChange={setPin}
              onSubmit={handleSubmitPin}
            />
          )}
          {view === 'loading' && <LoadingSummary />}
          {view === 'dashboard' && summary && (
            <Dashboard
              data={summary}
              onScanAnother={restart}
              onHome={returnHome}
              onVerifyInsurance={(version) => {
                setStaffIntakeId(undefined)
                setView(version === 'v2' ? 'insurance-v2' : 'insurance')
              }}
            />
          )}
          {view === 'insurance' && (
            <InsuranceVerification
              staffMode={!!staffIntakeId}
              staffIntakeId={staffIntakeId}
              onBack={
                staffIntakeId ? returnStaff : () => setView(summary ? 'dashboard' : 'landing')
              }
            />
          )}
          {view === 'insurance-v2' && (
            <InsuranceVerificationV2
              staffMode={!!staffIntakeId}
              staffIntakeId={staffIntakeId}
              onUseDocuments={
                staffIntakeId ? () => openStaffInsurance(staffIntakeId, 'v1') : undefined
              }
              onBack={
                staffIntakeId ? returnStaff : () => setView(summary ? 'dashboard' : 'landing')
              }
            />
          )}
          {view === 'commercial-estimator' && (
            <CommercialEstimator
              key={staffIntakeId}
              onResolvePatient={() => {
                returnStaff()
                setResolveEcwPatient(true)
              }}
              onBack={returnStaff}
              intakeId={staffIntakeId}
              refreshBenefits={refreshBenefits}
            />
          )}
        </Suspense>
      </main>
      <footer className="shell border-t border-[var(--line)] py-6 text-sm text-[var(--muted-ink)]">
        ClinicForce · Patient records and clinic insurance workspace
      </footer>
    </div>
  )
}
