export function patientCardFileProblem(files: File[]): string {
  if (!files.length || files.length > 2) return 'Choose one or two card files.'
  if (
    files.some(
      (file) =>
        !['image/jpeg', 'image/png', 'application/pdf'].includes(file.type) ||
        !file.size ||
        file.size > 10 * 1024 * 1024,
    )
  )
    return 'Use JPG, PNG, or PDF files, each no larger than 10 MB.'
  return ''
}
