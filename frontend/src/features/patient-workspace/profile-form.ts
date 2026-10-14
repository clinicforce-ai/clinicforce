import type { PatientProfile, PatientProfileInput } from '../../services/api/patient-session-types'

export type ProfileFormState = PatientProfileInput

export const emptyProfileForm: ProfileFormState = {
  given_name: '',
  family_name: '',
  birth_date: '',
  administrative_gender: 'unknown',
}

export function profileFormFrom(profile: PatientProfile): ProfileFormState {
  return {
    given_name: profile.given_name ?? profile.suggested_given_name ?? '',
    family_name: profile.family_name ?? profile.suggested_family_name ?? '',
    birth_date: profile.birth_date ?? '',
    administrative_gender: profile.administrative_gender ?? 'unknown',
  }
}

export function profileIsComplete(profile: PatientProfile | null): boolean {
  return Boolean(
    profile?.given_name &&
    profile.family_name &&
    profile.birth_date &&
    profile.administrative_gender,
  )
}
