export function requirementForLine<T extends { id?: string | null; code: string }>(
  rows: T[] | undefined,
  id: string | undefined,
  code: string,
): T | undefined {
  const exact = id ? rows?.find((row) => row.id === id) : undefined
  if (exact) return exact
  const sameCode =
    rows?.filter((row) => row.code === code && (!id || !row.id || row.id === code)) || []
  return sameCode.length === 1 ? sameCode[0] : undefined
}
