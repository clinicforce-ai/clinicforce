import type { PortalState } from '../portal-state'
import type { createPortalProviderActions } from '../portal-provider-actions'
import { FileText, LoaderCircle, RefreshCw } from 'lucide-react'
import { dateLabel } from '../display-text'
import {
  epicResourceGroupMeta,
  epicAvailabilityMessage,
  epicResourceDetail,
  groupEpicResources,
  hasEpicPdfLink,
} from '../fhir-display'
import { ProviderLogo } from '../ProviderLogo'

export function PortalEpic({
  state,
  provider,
}: {
  state: PortalState
  provider: ReturnType<typeof createPortalProviderActions>
}) {
  const {
    epicRecord,
    epicEndpoints,
    epicQuery,
    selectedEpicEndpointId,
    epicSearching,
    epicDirectoryError,
    workspaceTab,
    busy,
  } = state
  const {
    chooseEpicHospital,
    selectEpicHospital,
    connectEpic,
    cancelEpicConnection,
    refreshEpic,
    endEpicSession,
    openEpicDocument,
  } = provider
  const epicResources = (epicRecord?.bundle.entry ?? [])
    .map((entry) => entry.resource)
    .filter(Boolean)
  const epicCounts = epicResources.reduce<Record<string, number>>((counts, resource) => {
    const type = typeof resource.resourceType === 'string' ? resource.resourceType : 'FHIR resource'
    counts[type] = (counts[type] ?? 0) + 1
    return counts
  }, {})
  const selectedEpicEndpoint = epicEndpoints.find(
    (endpoint) => endpoint.id === selectedEpicEndpointId,
  )
  return (
    <section
      className="section-card"
      data-workspace-panel
      aria-labelledby="epic-heading"
      hidden={workspaceTab !== 'connections'}
    >
      <div className="section-heading">
        <div>
          <p className="section-kicker">Live patient-authorized connection</p>
          <h2 id="epic-heading">Epic / MyChart</h2>
        </div>
        <div className="provider-lockup">
          <ProviderLogo provider="mychart" />
        </div>
      </div>
      {!epicRecord ? (
        <>
          <p className="text-sm leading-6 text-[var(--muted-ink)]">
            Connect a hospital you have used to view its current Epic record in this browser.
            ClinicForce does not save Epic records, access tokens, or attached PDFs.
          </p>
          <div className="storage-choice mt-4">
            <div className="flex-1">
              <label htmlFor="epic-hospital">
                <strong>Hospital or health system</strong>
              </label>
              <input
                id="epic-hospital"
                value={epicQuery}
                onChange={(event) => chooseEpicHospital(event.target.value)}
                placeholder="Start typing, e.g. Houston Methodist"
                autoComplete="organization"
                aria-describedby="epic-hospital-help epic-hospital-options"
              />
              <div
                id="epic-hospital-options"
                className="epic-endpoint-options"
                role="listbox"
                aria-label="Matching hospitals"
              >
                {epicEndpoints.map((endpoint) => (
                  <button
                    key={endpoint.id}
                    className="epic-endpoint-option"
                    role="option"
                    type="button"
                    aria-selected={endpoint.id === selectedEpicEndpointId}
                    onClick={() => selectEpicHospital(endpoint)}
                  >
                    <strong>{endpoint.name}</strong>
                    {endpoint.aliases.length > 0 && (
                      <span>{endpoint.aliases.slice(0, 2).join(' · ')}</span>
                    )}
                  </button>
                ))}
              </div>
              <p id="epic-hospital-help" className="field-help mt-2" aria-live="polite">
                {epicSearching
                  ? 'Looking up Epic hospitals…'
                  : selectedEpicEndpoint
                    ? `Selected: ${selectedEpicEndpoint.name}. Continue to MyChart to authorize ClinicForce.`
                    : epicDirectoryError ||
                      (epicQuery.trim().length >= 2 && epicEndpoints.length === 0
                        ? 'No matching Epic hospital found. Try the hospital or health-system name.'
                        : 'Type at least two letters, then select a hospital.')}
              </p>
            </div>
            <button
              className="button button-primary"
              type="button"
              disabled={!selectedEpicEndpoint || busy === 'epic-connect'}
              onClick={() => selectedEpicEndpoint && void connectEpic(selectedEpicEndpoint)}
            >
              {busy === 'epic-connect' ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                'Continue to MyChart'
              )}
            </button>
          </div>
          {busy === 'epic-connect' && (
            <div className="feedback feedback-success mt-3" role="status">
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              <span>
                MyChart opened in a secure window. Complete sign-in there. If Epic shows an error,
                close that window or cancel here to return safely.
              </span>
              <button
                className="button button-secondary"
                type="button"
                onClick={cancelEpicConnection}
              >
                Cancel connection
              </button>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="storage-choice mt-4">
            <div>
              <strong>Live from {epicRecord.source.organization_name}</strong>
              <p>
                Retrieved {dateLabel(epicRecord.source.retrieved_at)}. This connection ends when you
                disconnect, its token expires, or the service restarts.
              </p>
            </div>
            <div className="storage-actions">
              <button
                className="button button-secondary"
                type="button"
                onClick={() => void refreshEpic()}
                disabled={busy === 'epic-load'}
              >
                {busy === 'epic-load' ? (
                  <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <RefreshCw className="size-4" aria-hidden="true" />
                )}{' '}
                Refresh
              </button>
              <button
                className="button button-secondary"
                type="button"
                onClick={() => void endEpicSession()}
              >
                Disconnect
              </button>
            </div>
          </div>
          <div className="workspace-stats mt-4" aria-label="Live Epic data counts">
            {Object.entries(epicCounts).map(([type, count]) => (
              <div key={type}>
                <FileText aria-hidden="true" />
                <span>
                  <strong>{count}</strong> {epicResourceGroupMeta[type]?.singular ?? type}
                  {count !== 1 ? 's' : ''}
                </span>
              </div>
            ))}
          </div>
          <p className="field-help mt-2">
            Availability: {epicAvailabilityMessage(epicRecord.availability)}
          </p>
          <div className="epic-grouped-records mt-4">
            {groupEpicResources(epicResources).map((group) => (
              <details key={group.type} className="epic-resource-group" open>
                <summary className="epic-group-header">
                  <span className="epic-group-label">{group.meta.label}</span>
                  <span className="epic-group-count">{group.resources.length}</span>
                </summary>
                <ul className="epic-resource-list">
                  {group.resources.map((resource, index) => {
                    const detail = epicResourceDetail(resource)
                    return (
                      <li
                        key={`${resource.resourceType ?? 'resource'}-${resource.id ?? index}`}
                        className="epic-resource-item"
                      >
                        <div className="epic-resource-content">
                          <strong className="epic-resource-title">{detail.title}</strong>
                          {detail.details.length > 0 && (
                            <div className="epic-resource-details">
                              {detail.details.map((line, i) => (
                                <span key={i} className="epic-detail-tag">
                                  {line}
                                </span>
                              ))}
                            </div>
                          )}
                          {resource.resourceType === 'DocumentReference' &&
                            typeof resource.id === 'string' &&
                            hasEpicPdfLink(resource) && (
                              <button
                                className="button button-secondary mt-3"
                                type="button"
                                disabled={busy === 'epic-document'}
                                onClick={() => void openEpicDocument(String(resource.id))}
                              >
                                Open source PDF
                              </button>
                            )}
                        </div>
                      </li>
                    )
                  })}
                </ul>
              </details>
            ))}
          </div>
        </>
      )}
    </section>
  )
}
