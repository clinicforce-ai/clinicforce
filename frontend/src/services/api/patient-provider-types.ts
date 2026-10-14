export interface EpicEndpoint {
  id: string
  name: string
  aliases: string[]
  addresses: Array<Record<string, unknown>>
}

export interface FhirResource {
  resourceType?: string
  id?: string
  [key: string]: unknown
}

export interface EpicRecord {
  bundle: { resourceType: string; entry: Array<{ resource: FhirResource }> }
  availability: Record<string, string>
  source: { organization_name: string; retrieved_at: string }
}

export interface EcwRecord {
  bundle: { resourceType: string; entry: Array<{ resource: FhirResource }> }
  availability: Record<string, string>
  source: { organization_name: string; practice_code: string; retrieved_at: string }
}
