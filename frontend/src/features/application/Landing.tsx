import { ArrowRight, HeartPulse, LockKeyhole, ShieldCheck, Stethoscope, UserRound } from 'lucide-react'

interface LandingProps { onChoosePatient: () => void; onChooseClinician: () => void }

export function Landing({ onChoosePatient, onChooseClinician }: LandingProps) {
  return <section className="landing-grid animate-enter" aria-labelledby="welcome-heading">
    <div className="landing-copy"><p className="eyebrow"><HeartPulse aria-hidden="true" className="size-4" /> One record, on your terms</p><h1 id="welcome-heading">Health information that stays in your hands.</h1><p className="lead">ClinicForce helps patients bring reports and health details together, then share an encrypted snapshot with a clinician when they choose.</p><div className="trust-list" aria-label="How ClinicForce protects your information"><p><LockKeyhole aria-hidden="true" /> A passcode protects every shared link</p><p><ShieldCheck aria-hidden="true" /> Source reports stay in secure document storage</p></div></div>
    <div className="role-panel" aria-label="Choose your portal"><p className="eyebrow">Choose a workspace</p><button className="role-card" type="button" onClick={onChoosePatient}><span className="role-icon patient-icon"><UserRound aria-hidden="true" /></span><span><strong>Patient workspace</strong><small>Add reports, organize health details, and create a QR code for your clinician.</small></span><ArrowRight aria-hidden="true" className="role-arrow" /></button><button className="role-card" type="button" onClick={onChooseClinician}><span className="role-icon doctor-icon"><Stethoscope aria-hidden="true" /></span><span><strong>Clinician access</strong><small>Scan a patient’s QR code and open the health summary they chose to share.</small></span><ArrowRight aria-hidden="true" className="role-arrow" /></button><p className="mt-5 text-sm leading-6 text-[var(--muted-ink)]">Use the patient workspace to create and manage your own account.</p></div>
  </section>
}
