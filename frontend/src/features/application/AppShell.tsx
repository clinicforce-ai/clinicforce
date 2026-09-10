import { lazy, Suspense, useEffect, useState, type FormEvent } from 'react'
import { decryptAndSummarize, type DoctorSummary } from '../../services/api'
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

export function AppShell() {
  const [view, setView] = useState<View>(() => new URLSearchParams(window.location.search).has('patient') ? 'patient' : 'landing')
  const [link, setLink] = useState<SmartHealthLink | null>(null)
  const [pin, setPin] = useState('')
  const [summary, setSummary] = useState<DoctorSummary | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { if (window.location.search) window.history.replaceState({}, '', window.location.pathname) }, [])
  const restart = () => { setView('scan'); setLink(null); setPin(''); setSummary(null); setError('') }
  const returnHome = () => { setView('landing'); setLink(null); setPin(''); setSummary(null); setError('') }
  const handleLinkScanned = (nextLink: SmartHealthLink) => { setLink(nextLink); setError(''); setView(isClinicforceIssuedLink(nextLink) ? 'pin' : 'external') }
  const handleSubmitPin = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (!link || !/^\d{4}$/.test(pin)) { setError('Enter the four-digit PIN supplied by the patient.'); return }; setError(''); setView('loading'); try { setSummary(await decryptAndSummarize(manifestIdFromUrl(link.manifestUrl), link.key, pin)); setView('dashboard') } catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to retrieve the patient file.'); setView('pin') } }
  return <div className="min-h-dvh bg-[var(--canvas)] text-[var(--ink)]"><a className="skip-link" href="#main-content">Skip to content</a><SiteHeader onHome={returnHome} /><main id="main-content" className="shell py-8 sm:py-12">{view === 'landing' && <Landing onChoosePatient={() => setView('patient')} onChooseClinician={() => setView('scan')} />}<Suspense fallback={<FeatureFallback />}>{view === 'patient' && <PatientPortal onBack={returnHome} />}{view === 'scan' && <Scanner onScanned={handleLinkScanned} onCancel={returnHome} />}{view === 'external' && link && <ExternalShlReceiver link={link} onCancel={restart} onImported={() => { setLink(null); setView('patient') }} />}{view === 'pin' && <AccessPin pin={pin} error={error} onBack={restart} onPinChange={setPin} onSubmit={handleSubmitPin} />}{view === 'loading' && <LoadingSummary />}{view === 'dashboard' && summary && <Dashboard data={summary} onScanAnother={restart} onHome={returnHome} onVerifyInsurance={(version) => setView(version === 'v2' ? 'insurance-v2' : 'insurance')} />}{view === 'insurance' && <InsuranceVerification onBack={() => setView(summary ? 'dashboard' : 'landing')} />}{view === 'insurance-v2' && <InsuranceVerificationV2 onBack={() => setView(summary ? 'dashboard' : 'landing')} />}</Suspense></main><footer className="shell border-t border-[var(--line)] py-6 text-sm text-[var(--muted-ink)]">ClinicForce · Patient-controlled record sharing</footer></div>
}
