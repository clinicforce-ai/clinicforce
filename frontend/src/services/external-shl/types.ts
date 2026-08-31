export interface ExternalShlReview {
  sourceHost: string
  label?: string
  resources: Record<string, unknown>[]
  resourceCounts: Array<{ type: string; count: number }>
  provenance: string[]
  conflicts: string[]
  apiAccessOffers: string[]
  canImport: boolean
}
