/**
 * Typed client for the Metreo API.
 *
 * The bearer token lives in `sessionStorage`, not in a cookie: this build has
 * no server-rendered authenticated page, so there is nothing to send a cookie
 * to, and sessionStorage dies with the tab. Moving to httpOnly cookies is part
 * of the OIDC work in phase 5.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1'
const TOKEN_KEY = 'metreo.token'
const CONTEXT_KEY = 'metreo.context'

export type Session = {
  token: string
  userId: string
  organizationId: string
  role: string
}

/** Un problème de champ, tel que FastAPI le rend sur un 422. */
export type FieldProblem = { readonly field: string; readonly message: string }

/**
 * Traduit la `detail` d'un 422 en problèmes de champ nommés.
 *
 * FastAPI rend une *liste*, pas un objet `{code, message}`. `ApiError` n'y
 * trouvait donc pas de `message` et retombait sur « Erreur HTTP 422 » : le
 * champ fautif, que le serveur avait nommé, n'était jamais montré.
 *
 * `loc` commence par l'origine (`body`, `query`, `path`) ; on la retire, elle
 * n'apprend rien à qui remplit un formulaire.
 */
function fieldProblems(detail: unknown): FieldProblem[] {
  if (!Array.isArray(detail)) return []
  return detail.flatMap((entry) => {
    const row = (entry ?? {}) as Record<string, unknown>
    const loc = Array.isArray(row.loc) ? row.loc : []
    const path = loc.filter((part) => part !== 'body' && part !== 'query' && part !== 'path')
    const message = typeof row.msg === 'string' ? row.msg : 'valeur refusée'
    return [{ field: path.join('.') || '(corps de la requête)', message }]
  })
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: unknown
  /** Non vide seulement pour un 422 de validation. */
  readonly fields: readonly FieldProblem[]

  constructor(status: number, detail: unknown) {
    const record = (detail ?? {}) as Record<string, unknown>
    const fields = fieldProblems(detail)
    const message =
      typeof record.message === 'string'
        ? record.message
        : fields.length > 0
          ? `${fields.length} champ(s) refusé(s)`
          : `Erreur HTTP ${status}`
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code =
      typeof record.code === 'string'
        ? record.code
        : fields.length > 0
          ? 'validation_error'
          : 'http_error'
    this.detail = detail
    this.fields = fields
  }
}

/**
 * Une session expirée met fin à la session, où qu'elle soit constatée.
 *
 * `Shell` traitait déjà le cas, mais seulement à son montage, sur son appel à
 * `/auth/me`. Une expiration survenue *pendant* la navigation tombait dans le
 * `catch` de la page, qui se contentait d'afficher l'erreur : la session
 * restait en place et l'utilisateur restait sur des données périmées.
 *
 * Seul `token_expired` déclenche la déconnexion. Un `401` d'une autre cause,
 * ou un `403`, laisse la session intacte : elle est valide, c'est l'action qui
 * ne l'est pas.
 */
function endSessionIfExpired(error: ApiError): void {
  if (error.status !== 401 || error.code !== 'token_expired') return
  if (typeof window === 'undefined') return
  clearSession()
  // `replace` et non `assign` : la page périmée ne doit pas rester dans
  // l'historique, sinon « précédent » y ramène sans jeton.
  window.location.replace('/')
}

export function loadSession(): Session | null {
  if (typeof window === 'undefined') return null
  const token = window.sessionStorage.getItem(TOKEN_KEY)
  const raw = window.sessionStorage.getItem(CONTEXT_KEY)
  if (!token || !raw) return null
  try {
    return { token, ...(JSON.parse(raw) as Omit<Session, 'token'>) }
  } catch {
    return null
  }
}

export function storeSession(session: Session): void {
  window.sessionStorage.setItem(TOKEN_KEY, session.token)
  window.sessionStorage.setItem(
    CONTEXT_KEY,
    JSON.stringify({
      userId: session.userId,
      organizationId: session.organizationId,
      role: session.role,
    }),
  )
}

export function clearSession(): void {
  window.sessionStorage.removeItem(TOKEN_KEY)
  window.sessionStorage.removeItem(CONTEXT_KEY)
}

type RequestOptions = {
  method?: string
  body?: unknown
  formData?: FormData
  raw?: boolean
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const session = loadSession()
  const headers: Record<string, string> = {}
  if (session) headers.Authorization = `Bearer ${session.token}`
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  const response = await fetch(`${API_URL}${path}`, {
    method: options.method ?? 'GET',
    headers,
    body: options.formData ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined),
    cache: 'no-store',
  })

  if (!response.ok) {
    let detail: unknown = null
    try {
      detail = (await response.json()).detail
    } catch {
      detail = { message: await response.text() }
    }
    const error = new ApiError(response.status, detail)
    endSessionIfExpired(error)
    throw error
  }
  if (response.status === 204) return undefined as T
  if (options.raw) return (await response.text()) as T
  return (await response.json()) as T
}

export const api = {
  url: API_URL,

  devLogin: (email: string, organizationId?: string) =>
    request<{
      access_token: string
      user_id: string
      organization_id: string
      role: string
    }>('/auth/dev-login', {
      method: 'POST',
      body: { email, organization_id: organizationId ?? null },
    }),

  me: () => request<Me>('/auth/me'),
  health: () => request<Health>('/health'),
  organization: () => request<Organization>('/organization'),
  organizationSettings: () => request<OrgSettings>('/organization/settings'),

  projects: (query = '') => request<Page<Project>>(`/projects${query}`),
  project: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (body: Record<string, unknown>) =>
    request<Project>('/projects', { method: 'POST', body }),

  boqs: (projectId: string) => request<Boq[]>(`/projects/${projectId}/boqs`),
  createBoq: (projectId: string, body: Record<string, unknown>) =>
    request<Boq>(`/projects/${projectId}/boqs`, { method: 'POST', body }),
  boqItems: (boqId: string) => request<BoqItem[]>(`/boqs/${boqId}/items`),
  createBoqItem: (boqId: string, body: Record<string, unknown>) =>
    request<BoqItem>(`/boqs/${boqId}/items`, { method: 'POST', body }),

  priceBooks: () => request<PriceBook[]>('/price-books'),
  priceBookVersions: (bookId: string) =>
    request<PriceBookVersion[]>(`/price-books/${bookId}/versions`),
  priceItems: (versionId: string, query = '') =>
    request<Page<PriceItem>>(`/price-books/versions/${versionId}/items${query}`),
  previewImport: (versionId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ImportReport>(`/price-books/versions/${versionId}/imports/preview`, {
      method: 'POST',
      formData: form,
    })
  },
  commitImport: (batchId: string, strategy: string) =>
    request<ImportOutcome>(`/price-books/imports/${batchId}/commit`, {
      method: 'POST',
      body: { strategy, confirm: true },
    }),

  estimates: (projectId?: string) =>
    request<Estimate[]>(`/estimates${projectId ? `?project_id=${projectId}` : ''}`),
  estimate: (id: string) => request<Estimate>(`/estimates/${id}`),
  estimateVersions: (id: string) => request<EstimateVersion[]>(`/estimates/${id}/versions`),
  computation: (estimateId: string, versionId: string) =>
    request<Computation>(`/estimates/${estimateId}/versions/${versionId}/computation`),
  freeze: (estimateId: string, versionId: string, label?: string) =>
    request<EstimateVersion>(`/estimates/${estimateId}/versions/${versionId}/freeze`, {
      method: 'POST',
      body: { confirm: true, label: label ?? null },
    }),
  exportUrl: (estimateId: string, versionId: string, kind: 'csv' | 'internal' | 'quote') => {
    const suffix =
      kind === 'quote'
        ? 'quote.html'
        : kind === 'internal'
          ? 'export.csv?include_internal=true'
          : 'export.csv'
    return `${API_URL}/estimates/${estimateId}/versions/${versionId}/${suffix}`
  },
  download: (path: string) => request<string>(path, { raw: true }),

  /**
   * Télécharge un export en conservant l'enveloppe d'erreur de l'API.
   *
   * La page appelait `fetch` elle-même et levait une `Error` nue : le
   * `required_permission` que l'API fournit sur un 403 était jeté, et
   * l'utilisateur lisait « Erreur HTTP 403 » sans savoir ce qui lui manquait.
   * Passer par ici lui rend le motif, et applique la même fin de session sur
   * jeton expiré que les lectures JSON.
   */
  fetchExport: async (url: string): Promise<Blob> => {
    const session = loadSession()
    const response = await fetch(url, {
      headers: session ? { Authorization: `Bearer ${session.token}` } : {},
      cache: 'no-store',
    })
    if (!response.ok) {
      let detail: unknown = null
      try {
        detail = (await response.json()).detail
      } catch {
        detail = { message: `Erreur HTTP ${response.status}` }
      }
      const error = new ApiError(response.status, detail)
      endSessionIfExpired(error)
      throw error
    }
    return response.blob()
  },

  auditEvents: (query = '') => request<Page<AuditEvent>>(`/audit/events${query}`),
  auditVerify: () => request<AuditVerify>('/audit/verify'),
}

// --- types mirroring the OpenAPI document ---------------------------------

export type Page<T> = { items: T[]; page: { total: number; limit: number; offset: number } }

export type Health = {
  status: string
  environment: string
  version: string
  ai_enabled: boolean
  database: string
  configuration_problems: string[]
}

export type Me = {
  user_id: string
  email: string
  full_name: string
  organization_id: string
  organization_name: string
  role: string
  role_label: string
  permissions: string[]
  memberships: { organization_id: string; organization_name: string; role_label: string }[]
}

export type Organization = {
  id: string
  name: string
  legal_name: string | null
  company_number: string | null
  country_code: string
  region_code: string
  currency: string
}

export type OrgSettings = {
  rounding_scale: number
  rounding_mode: string
  unit_price_scale: number
  commercial_rates_visible: boolean
  site_overheads_rate: string | null
  general_overheads_rate: string | null
  contingency_rate: string | null
  margin_rate: string | null
  margin_method: string | null
  missing_price_policy: string
}

export type Project = {
  id: string
  reference: string
  client_reference: string | null
  name: string
  client_name: string | null
  city: string | null
  country_code: string
  region_code: string
  submission_deadline: string | null
  currency: string
  status: string
}

export type Boq = { id: string; project_id: string; name: string; source: string; revision: number }

export type BoqItem = {
  id: string
  position: string
  designation: string
  unit_code: string
  quantity: string
  kind: string
  status: string
  formula: string | null
  price_item_id: string | null
  composite_price_id: string | null
}

export type PriceBook = { id: string; name: string; currency: string; is_default: boolean }
export type PriceBookVersion = {
  id: string
  version_number: number
  label: string | null
  status: string
}

export type PriceItem = {
  id: string
  code: string
  label: string
  family: string | null
  resource_kind: string
  unit_code: string
  unit_price: string
  currency: string
  supplier_name: string | null
  is_demo_data: boolean
}

export type ImportRow = {
  line_number: number
  is_valid: boolean
  is_duplicate: boolean
  errors: { column: string | null; code: string; message: string }[]
  normalized: Record<string, unknown> | null
  raw: Record<string, string>
}

export type ImportReport = {
  batch_id: string
  filename: string
  status: string
  row_count: number
  valid_count: number
  error_count: number
  duplicate_count: number
  column_mapping: Record<string, string>
  meta: {
    delimiter: string | null
    encoding: string
    unmapped_headers: string[]
    missing_required_columns: string[]
    fatal: string | null
  }
  rows: ImportRow[]
}

export type ImportOutcome = {
  created: number
  updated: number
  skipped: number
  conflicted: number
  strategy: string
}

export type Estimate = {
  id: string
  project_id: string
  boq_id: string
  price_book_version_id: string
  name: string
  currency: string
}

export type EstimateVersion = {
  id: string
  version_number: number
  label: string | null
  status: string
  total_selling_price_ht: string | null
  /**
   * Le Total HT **du document** — la même valeur que le devis imprime.
   * `null` sur une version gelée ancienne dont le total imprimé n'a pas pu
   * être reconstruit : afficher alors une absence, jamais l'arrondi du brut.
   */
  total_selling_price_ht_display: string | null
  total_ttc_display: string | null
  document_totals_available: boolean
  snapshot_sha256: string | null
  frozen_at: string | null
}

export type Component = {
  label: string
  kind: string
  kind_label: string
  resource_quantity: string
  resource_unit: string
  unit_price: string
  amount: string
  formula: string
  density_source: string | null
}

export type MarkupStep = {
  key: string
  label: string
  base_amount: string
  rate: string
  amount: string
  running_total: string
  formula: string
}

export type LinePrice = {
  unit_price_ht: string
  selling_price_ht: string
  direct_cost?: string
  cost_price?: string
  components?: Component[]
  markup_steps?: MarkupStep[]
  cost_by_kind?: Record<string, string>
}

export type EstimateLine = {
  line_id: string
  code: string
  designation: string
  kind: string
  quantity: string
  unit: string
  missing_price: boolean
  included_in_total: boolean
  price: LinePrice | null
}

export type EstimateResult = {
  currency: string
  lines: EstimateLine[]
  total_direct_cost?: string
  total_cost_price?: string
  total_selling_price_ht: string
  options_total_ht: string
  taxes: { code: string; label: string; rate: string; amount: string }[]
  total_ttc: string
  missing_price_line_ids: string[]
  blocking: boolean
}

export type Computation = {
  version: EstimateVersion
  computed_at: string
  from_snapshot: boolean
  includes_internal_costs: boolean
  result: EstimateResult
}

export type AuditEvent = {
  id: string
  sequence: number
  occurred_at: string
  actor_email: string | null
  action: string
  object_type: string
  object_id: string | null
  summary: string
  hash: string
}

export type AuditVerify = {
  valid: boolean
  checked: number
  head_hash: string | null
  failed_at_sequence: number | null
  reason: string | null
}
