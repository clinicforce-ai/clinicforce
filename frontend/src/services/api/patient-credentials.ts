export function credentialsPayload(patientId?: string, password?: string): Record<string, string> {
  return patientId && password ? { patient_id: patientId.trim(), password } : {}
}
