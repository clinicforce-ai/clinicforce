import { ShieldCheck } from 'lucide-react'
import clinicforceLogo from '../../assets/clinicforce-logo.png'

interface SiteHeaderProps { onHome: () => void; staffMode?: boolean }

export function SiteHeader({ onHome, staffMode = false }: SiteHeaderProps) {
  return <header className="site-header"><div className="shell flex min-h-20 items-center justify-between gap-4">
    <button className="brand" type="button" onClick={onHome} aria-label="ClinicForce home"><img src={clinicforceLogo} alt="ClinicForce" width="180" height="48" /><span className="hidden border-l border-[var(--line)] pl-4 text-left text-xs font-semibold uppercase tracking-[0.15em] text-[var(--muted-ink)] sm:block">{staffMode ? 'Clinic insurance' : 'Personal health records'}</span></button>
    <div className="flex items-center gap-2 text-sm font-semibold text-[var(--muted-ink)]"><ShieldCheck aria-hidden="true" className="size-5 text-[var(--green)]" /><span className="hidden sm:inline">{staffMode ? 'Staff access' : 'Private, patient-controlled'}</span></div>
  </div></header>
}
