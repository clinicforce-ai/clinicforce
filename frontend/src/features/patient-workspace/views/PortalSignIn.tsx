import type { PortalState } from '../portal-state'
import type { createPortalAccountActions } from '../portal-account-actions'
import {
  ArrowLeft,
  CheckCircle2,
  ChevronRight,
  Eye,
  EyeOff,
  LockKeyhole,
  LoaderCircle,
  ShieldCheck,
  X,
} from 'lucide-react'
import { ProviderLogo } from '../ProviderLogo'

export function PortalSignIn({
  state,
  account,
  onBack,
}: {
  state: PortalState
  account: ReturnType<typeof createPortalAccountActions>
  onBack: () => void
}) {
  const {
    mode,
    setMode,
    patientId,
    setPatientId,
    password,
    setPassword,
    showPassword,
    setShowPassword,
    idmeStatusLoading,
    busy,
    restoringSession,
    error,
    notice,
    clearFeedback,
    idmeReady,
  } = state
  const { authenticate, signInWithGoogle, continueWithIdme } = account
  return (
    <section className="auth-layout animate-enter" aria-labelledby="patient-heading">
      <button className="back-control" type="button" onClick={onBack}>
        <ArrowLeft aria-hidden="true" className="size-4" /> Back to home
      </button>
      <div className="auth-aside">
        <p className="eyebrow">
          <ShieldCheck aria-hidden="true" className="size-4" /> Patient workspace
        </p>
        <h1 id="patient-heading">Build a record you can carry into care.</h1>
        <p className="lead-small">
          Add source reports and the health details you know. When you are ready, create a QR code
          for your clinician to scan.
        </p>
        <div className="auth-note">
          <LockKeyhole aria-hidden="true" />
          <p>
            <strong>You remain in control.</strong>
            <br />
            Share only after you choose a passcode and show the QR code.
          </p>
        </div>
      </div>
      <div className="panel auth-panel">
        <div className="mode-tabs" role="tablist" aria-label="Patient account options">
          <button
            className={mode === 'login' ? 'active' : ''}
            role="tab"
            aria-selected={mode === 'login'}
            type="button"
            onClick={() => {
              setMode('login')
              clearFeedback()
            }}
          >
            Sign in
          </button>
          <button
            className={mode === 'register' ? 'active' : ''}
            role="tab"
            aria-selected={mode === 'register'}
            type="button"
            onClick={() => {
              setMode('register')
              clearFeedback()
            }}
          >
            Create account
          </button>
        </div>
        <h2>{mode === 'login' ? 'Welcome back' : 'Create your workspace'}</h2>
        <p className="text-sm leading-6 text-[var(--muted-ink)]">
          {mode === 'login'
            ? 'Enter your account details to open your health record.'
            : 'Use a memorable patient ID and a strong password.'}
        </p>
        {notice && (
          <div className="feedback feedback-success" role="status">
            <CheckCircle2 aria-hidden="true" />
            <span>{notice}</span>
            <button type="button" onClick={clearFeedback} aria-label="Dismiss message">
              <X aria-hidden="true" className="size-4" />
            </button>
          </div>
        )}
        {restoringSession ? (
          <div className="status-panel">
            <LoaderCircle className="size-5 animate-spin" aria-hidden="true" />
            <p>Opening your secure workspace…</p>
          </div>
        ) : (
          <form className="stack-form" onSubmit={authenticate} noValidate>
            <label htmlFor="patient-id">Patient ID</label>
            <input
              id="patient-id"
              autoComplete="username"
              value={patientId}
              onChange={(event) => setPatientId(event.target.value)}
              placeholder="e.g. alex-morgan"
            />
            <label htmlFor="patient-password">Password</label>
            <div className="password-field">
              <input
                id="patient-password"
                type={showPassword ? 'text' : 'password'}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                aria-describedby={mode === 'register' ? 'password-help' : undefined}
              />
              <button
                className="icon-button"
                type="button"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
              </button>
            </div>
            {mode === 'register' && (
              <p id="password-help" className="field-help">
                Use at least 12 characters.
              </p>
            )}
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <button
              className="button button-primary w-full"
              type="submit"
              disabled={busy === 'auth'}
            >
              {busy === 'auth' ? (
                <>
                  <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> Opening
                  workspace
                </>
              ) : (
                <>
                  {mode === 'login' ? 'Open my workspace' : 'Create my workspace'}{' '}
                  <ChevronRight aria-hidden="true" className="size-4" />
                </>
              )}
            </button>
          </form>
        )}
        {!restoringSession && (
          <>
            <div className="divider">
              <span>or</span>
            </div>
            <button
              className="button google-signin-button w-full"
              type="button"
              disabled={busy === 'google' || busy === 'idme'}
              onClick={() => void signInWithGoogle()}
            >
              {busy === 'google' ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <ProviderLogo provider="google" />
              )}{' '}
              Continue with Google
            </button>
            <p className="field-help">
              Google sign-in opens your patient workspace and stores new PDFs in your ClinicForce
              Drive folder.
            </p>
            {mode === 'login' ? (
              <>
                <button
                  className="button button-secondary w-full mt-3"
                  type="button"
                  aria-describedby="idme-login-help"
                  disabled={idmeStatusLoading || !idmeReady || busy === 'google' || busy === 'idme'}
                  onClick={() => void continueWithIdme('login')}
                >
                  {busy === 'idme' ? (
                    <>
                      <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> Opening
                      ID.me…
                    </>
                  ) : idmeStatusLoading ? (
                    <>
                      <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> Checking
                      ID.me…
                    </>
                  ) : (
                    <>
                      <ShieldCheck className="size-4" aria-hidden="true" /> Continue with ID.me
                    </>
                  )}
                </button>
                <p id="idme-login-help" className="field-help">
                  {idmeReady
                    ? 'ID.me is a secure ClinicForce sign-in for accounts that linked it first. Epic, MyChart, and healow authorization remains separate.'
                    : 'ID.me sign-in is not available right now. Use your patient ID or Google instead.'}
                </p>
              </>
            ) : (
              <p className="field-help mt-3">
                To use ID.me, first create or sign in to your ClinicForce account, then link ID.me
                under Care connections.
              </p>
            )}
          </>
        )}
      </div>
    </section>
  )
}
