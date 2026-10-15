export interface LinkResponse {
  shl_uri: string
  qr_code_base64: string
  epic_included?: boolean
  share_mode?: 'standard' | 'ecw_compat'
}
