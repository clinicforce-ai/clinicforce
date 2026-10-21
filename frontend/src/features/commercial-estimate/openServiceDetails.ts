export function openServiceDetails() {
  const detail = document.querySelector<HTMLDetailsElement>('#visit-detail-services')
  if (detail) detail.open = true
  detail?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
