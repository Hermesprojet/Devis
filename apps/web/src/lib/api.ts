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

/**
 * Une requête PUBLIQUE : cookie de session, jamais de jeton porteur.
 *
 * Elle ne passe pas par `request` parce qu'elle ne doit surtout pas emporter
 * le jeton de l'entreprise : la page publique peut être ouverte dans le même
 * navigateur qu'une session Metreo, et rien n'autorise à confondre les deux
 * identités.
 */
async function publicRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {}
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  const response = await fetch(`${API_URL}${path}`, {
    method: options.method ?? 'GET',
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    cache: 'no-store',
    credentials: 'include',
  })
  if (!response.ok) {
    let detail: unknown = null
    try {
      detail = (await response.json()).detail
    } catch {
      detail = { message: `Erreur HTTP ${response.status}` }
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/**
 * Les octets d'une route authentifiée, rendus tels quels.
 *
 * `request` suppose du JSON ; ces routes rendent un fichier. La gestion des
 * erreurs reste la même — même forme de `detail`, même fin de session sur un
 * jeton périmé — parce que deux traitements divergeraient au premier code
 * ajouté.
 */
/**
 * La RÉPONSE d'un appel authentifié, en-têtes compris.
 *
 * Séparée de `octetsAuthentifies` parce qu'une tuile de plan ne transporte pas
 * que des pixels : elle dit aussi quelle zone elle couvre, et cette
 * information vit dans les en-têtes. Un appelant qui n'a besoin que des octets
 * continue d'appeler la seconde.
 */
async function reponseAuthentifiee(chemin: string): Promise<Response> {
  const session = loadSession()
  const response = await fetch(`${API_URL}${chemin}`, {
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
  return response
}

async function octetsAuthentifies(chemin: string): Promise<Blob> {
  return (await reponseAuthentifiee(chemin)).blob()
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

  oidcStart: (returnTo?: string) =>
    request<{ authorization_url: string }>(
      `/auth/oidc/start${returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : ''}`,
    ),

  // Le jeton arrive ici, dans un corps de réponse, et nulle part ailleurs. Le
  // navigateur ne rapporte du fournisseur qu'un code opaque à usage unique.
  oidcExchange: (loginCode: string, organizationId?: string) =>
    request<{
      access_token: string
      user_id: string
      organization_id: string
      role: string
    }>('/auth/oidc/exchange', {
      method: 'POST',
      body: { login_code: loginCode, organization_id: organizationId ?? null },
    }),

  me: () => request<Me>('/auth/me'),
  health: () => request<Health>('/health'),
  organization: () => request<Organization>('/organization'),
  updateOrganization: (body: Record<string, unknown>) =>
    request<Organization>('/organization', { method: 'PATCH', body }),
  /**
   * Téléverse le logo. Le fichier part TEL QUEL, sans conversion.
   *
   * Le navigateur ne se prononce ni sur le format ni sur les dimensions : le
   * serveur décode les octets et tranche. Vérifier ici en plus donnerait deux
   * verdicts à tenir d'accord, et celui du navigateur ne protège personne —
   * il se contourne en postant directement.
   */
  uploadLogo: (file: File) => {
    const corps = new FormData()
    corps.append('file', file)
    return request<Organization>('/organization/logo', { method: 'PUT', formData: corps })
  },
  deleteLogo: () => request<Organization>('/organization/logo', { method: 'DELETE' }),
  /**
   * Les octets du logo, rapportés AVEC le jeton.
   *
   * Une balise `<img src>` émet une requête nue : le navigateur n'y joint
   * aucun en-tête, et la route — qui exige `Authorization: Bearer` — répond
   * 401. L'écran affichait donc une image cassée là où il annonçait un logo.
   * On rapatrie les octets par `fetch`, et la balise reçoit une URL d'objet.
   *
   * La page publique, elle, s'authentifie par cookie : le navigateur l'envoie
   * de lui-même, et une balise ordinaire y suffit.
   */
  /**
   * Le classeur vide à remplir, rapatrié AVEC le jeton.
   *
   * Un lien nu n'emporte aucun en-tête : la route exige une session et
   * répondrait 401, et l'utilisateur verrait un téléchargement échouer sans
   * savoir pourquoi.
   */
  importTemplate: (): Promise<Blob> => octetsAuthentifies('/price-books/imports/modele.xlsx'),
  /** Les octets du logo, rapatriés avec le jeton — voir `octetsAuthentifies`. */
  logoBlob: (): Promise<Blob> => octetsAuthentifies('/organization/logo'),
  organizationSettings: () => request<OrgSettings>('/organization/settings'),
  updateOrganizationSettings: (body: Record<string, unknown>) =>
    request<OrgSettings>('/organization/settings', { method: 'PATCH', body }),
  /** Ce qu'un motif produirait, jugé par le SERVEUR — jamais recalculé ici. */
  quoteNumberPreview: (pattern: string) =>
    request<QuoteNumberPreview>(
      `/organization/quote-number-preview?pattern=${encodeURIComponent(pattern)}`,
    ),

  members: () => request<Member[]>('/organization/members'),
  inviteMember: (body: Record<string, unknown>) =>
    request<Member>('/organization/members', { method: 'POST', body }),
  updateMember: (membershipId: string, body: Record<string, unknown>) =>
    request<Member>(`/organization/members/${membershipId}`, { method: 'PATCH', body }),

  taxRates: () => request<TaxRate[]>('/organization/tax-rates'),
  createTaxRate: (body: Record<string, unknown>) =>
    request<TaxRate>('/organization/tax-rates', { method: 'POST', body }),
  updateTaxRate: (id: string, body: Record<string, unknown>) =>
    request<TaxRate>(`/organization/tax-rates/${id}`, { method: 'PATCH', body }),
  deleteTaxRate: (id: string) =>
    request<void>(`/organization/tax-rates/${id}`, { method: 'DELETE' }),

  projects: (query = '') => request<Page<Project>>(`/projects${query}`),
  project: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (body: Record<string, unknown>) =>
    request<Project>('/projects', { method: 'POST', body }),
  updateProject: (id: string, body: Record<string, unknown>) =>
    request<Project>(`/projects/${id}`, { method: 'PATCH', body }),

  clients: (query = '') => request<Client[]>(`/clients${query}`),
  client: (id: string) => request<Client>(`/clients/${id}`),
  createClient: (body: Record<string, unknown>) =>
    request<Client>('/clients', { method: 'POST', body }),
  updateClient: (id: string, body: Record<string, unknown>) =>
    request<Client>(`/clients/${id}`, { method: 'PATCH', body }),
  archiveClient: (id: string) => request<void>(`/clients/${id}`, { method: 'DELETE' }),

  documents: (projectId: string, includeArchived = false) =>
    request<DocumentSummary[]>(
      `/projects/${projectId}/documents${includeArchived ? '?include_archived=true' : ''}`,
    ),
  createDocument: (projectId: string, body: Record<string, unknown>) =>
    request<DocumentSummary>(`/projects/${projectId}/documents`, { method: 'POST', body }),
  setDocumentStatus: (documentId: string, status: 'active' | 'archived') =>
    request<DocumentSummary>(`/documents/${documentId}`, { method: 'PATCH', body: { status } }),
  documentRevisions: (documentId: string) =>
    request<DocumentRevision[]>(`/documents/${documentId}/revisions`),
  revisionContentUrl: (documentId: string, revisionId: string) =>
    `${API_URL}/documents/${documentId}/revisions/${revisionId}/content`,

  /**
   * Dépose un fichier, en rendant l'avancement de l'envoi.
   *
   * `XMLHttpRequest` et non `fetch` : seul le premier expose la progression de
   * l'ENVOI. Sur 25 Mio derrière une connexion de chantier, une barre qui
   * avance est la différence entre attendre et croire que c'est planté.
   */
  uploadRevision: (
    documentId: string,
    file: File,
    onProgress?: (pourcent: number) => void,
  ): Promise<DocumentRevision> =>
    new Promise((resolve, reject) => {
      const session = loadSession()
      const corps = new FormData()
      corps.append('file', file)
      const requete = new XMLHttpRequest()
      requete.open('POST', `${API_URL}/documents/${documentId}/revisions`)
      if (session) requete.setRequestHeader('Authorization', `Bearer ${session.token}`)
      requete.upload.onprogress = (evenement) => {
        if (evenement.lengthComputable && onProgress) {
          onProgress(Math.round((evenement.loaded / evenement.total) * 100))
        }
      }
      requete.onload = () => {
        let charge: unknown = null
        try {
          charge = JSON.parse(requete.responseText)
        } catch {
          charge = null
        }
        if (requete.status >= 200 && requete.status < 300) {
          resolve(charge as DocumentRevision)
          return
        }
        // La même enveloppe d'erreur que `request` : sans elle, l'utilisateur
        // lirait « Erreur HTTP 422 » là où l'API nomme précisément le refus.
        const detail = (charge as { detail?: unknown } | null)?.detail ?? {
          message: `Erreur HTTP ${requete.status}`,
        }
        const erreur = new ApiError(requete.status, detail)
        endSessionIfExpired(erreur)
        reject(erreur)
      }
      requete.onerror = () => reject(new ApiError(0, { message: 'Envoi interrompu.' }))
      requete.onabort = () => reject(new ApiError(0, { message: 'Envoi annulé.' }))
      requete.send(corps)
    }),

  /**
   * Lance la lecture déterministe d'un plan DXF. **Elle est SYNCHRONE.**
   *
   * Mesuré : 7 à 9 secondes sur deux plans réels de 7 et 11 Mo. L'écran
   * l'annonce AVANT de lancer et montre une attente explicite pendant —
   * sans quoi l'utilisateur conclut que c'est planté et rappelle la route,
   * ce que le serveur refuse ensuite par un 409 `etape_deja_reussie`.
   *
   * Rend le même corps que `lirePlan` : le constat est disponible sans
   * second aller-retour.
   */
  analyserLePlan: (documentId: string, revisionId: string) =>
    request<PlanLu>(`/documents/${documentId}/revisions/${revisionId}/plan/analyse`, {
      method: 'POST',
    }),
  /**
   * Le constat de lecture et les mesures proposées.
   *
   * `404` avec le code `plan_non_analyse` quand la lecture n'a jamais été
   * lancée : c'est un état normal du parcours, pas une panne. L'écran le
   * distingue d'une vraie erreur et propose l'analyse.
   */
  lirePlan: (documentId: string, revisionId: string) =>
    request<PlanLu>(`/documents/${documentId}/revisions/${revisionId}/plan`),
  /**
   * Les octets du rendu du plan — un SVG — rapatriés AVEC le jeton.
   *
   * Même raison que le logo : une balise `<img src>` émet une requête NUE,
   * sans en-tête `Authorization`, et la route répond 401. Voir
   * `octetsAuthentifies`, et le commentaire de `LecturePlan` sur la raison
   * pour laquelle ce SVG est chargé comme IMAGE et jamais inséré en ligne.
   */
  imageDuPlan: (documentId: string, revisionId: string): Promise<Blob> =>
    octetsAuthentifies(`/documents/${documentId}/revisions/${revisionId}/plan/image`),
  /** Les textes extraits d'un PDF, situés sur son aperçu. */
  textesDuPlan: (documentId: string, revisionId: string, page?: number) =>
    request<TextesDePlan>(
      `/documents/${documentId}/revisions/${revisionId}/plan/textes` +
        (page ? `?page=${page}` : ''),
    ),

  /** L'aperçu d'une PAGE. Le DXF ignore le paramètre : il n'en a qu'une. */
  renduDeLaPage: (documentId: string, revisionId: string, page: number) =>
    octetsAuthentifies(
      `/documents/${documentId}/revisions/${revisionId}/plan/image?page=${page}`,
    ),

  /**
   * L'agrandissement d'une zone, pour la RELIRE.
   *
   * **Lente la première fois, instantanée ensuite.** Mesuré sur quatre plans
   * réels : 0,2 à 5,3 secondes au premier appel — le chargement de la page par
   * PDFium — puis 0,3 milliseconde depuis le cache du volume. L'écran doit
   * donc annoncer l'attente, et ne pas la relancer à chaque mouvement de
   * souris.
   */
  tuileDuPlan: async (
    documentId: string,
    revisionId: string,
    page: number,
    zone: [number, number, number, number],
  ): Promise<TuileDePlan> => {
    const reponse = await reponseAuthentifiee(
      `/documents/${documentId}/revisions/${revisionId}/plan/tuile` +
        `?page=${page}&x0=${zone[0]}&y0=${zone[1]}&x1=${zone[2]}&y1=${zone[3]}`,
    )
    // **La zone que l'image couvre VRAIMENT**, et non celle demandée.
    //
    // Un bitmap se compte en pixels entiers : le rendu tronque, et l'image
    // couvre un peu moins que la fenêtre demandée — mesuré, 0,2 % de moins en
    // largeur et 0,28 % en hauteur sur une loupe de 21 × 16 points. Placer un
    // clic sur la zone demandée introduit donc une erreur petite, systématique,
    // et qui entre dans l'échelle déclarée avant de multiplier toutes les
    // mesures de la page.
    //
    // En-tête absent : le serveur est plus ancien que cet écran, ou un proxy
    // l'a filtré. On retombe sur la zone demandée en le DISANT, pour qu'un
    // pointage décalé ne reste pas sans explication.
    const brut = reponse.headers.get('X-Metreo-Zone')
    const nombres = brut?.split(',').map(Number) ?? []
    const zoneConnue = nombres.length === 4 && nombres.every((n) => Number.isFinite(n))
    const pixels = reponse.headers.get('X-Metreo-Pixels')?.split(',').map(Number) ?? []
    return {
      blob: await reponse.blob(),
      zone: zoneConnue ? (nombres as [number, number, number, number]) : zone,
      zoneDeclaree: zoneConnue,
      pixels:
        pixels.length === 2 && pixels.every((n) => Number.isFinite(n))
          ? (pixels as [number, number])
          : null,
      depuisLeCache: reponse.headers.get('X-Metreo-Tuile') === 'cache',
    }
  },

  /** Les échelles déclarées et les mesures prises sur un PDF. */
  mesuresDuPdf: (documentId: string, revisionId: string) =>
    request<MesuresDePdf>(
      `/documents/${documentId}/revisions/${revisionId}/plan/mesures`,
    ),

  /**
   * Déclarer l'échelle d'une page.
   *
   * `resolution_du_pointage` n'est pas un détail : c'est elle qui décide de la
   * confiance accordée à toutes les mesures qui suivront. Mesuré, un pixel de
   * l'aperçu pleine page d'un A0 vaut 12 à 42 mm d'ouvrage ; sur une tuile
   * agrandie, 0,6 à 6 mm.
   */
  calibrerLePlan: (
    documentId: string,
    revisionId: string,
    body: {
      page: number
      premier: PointDEcran
      second: PointDEcran
      distance_reelle: string
      unite: string
      resolution_du_pointage: string
      motif: string
      zone?: [number, number, number, number]
    },
  ) =>
    request<CalibrationDePlan>(
      `/documents/${documentId}/revisions/${revisionId}/plan/calibration`,
      { method: 'POST', body },
    ),

  /** Mesurer un segment ou une surface sur un PDF calibré. */
  mesurerSurLePdf: (
    documentId: string,
    revisionId: string,
    body: {
      page: number
      type: 'segment' | 'surface'
      points: PointDEcran[]
      libelle: string
      /** Points PostScript par pixel AFFICHÉ, mesurés au clic. */
      resolution_du_pointage?: string
    },
  ) =>
    request<MesureDePdf>(
      `/documents/${documentId}/revisions/${revisionId}/plan/mesures`,
      { method: 'POST', body },
    ),

  /**
   * La décision humaine sur une proposition d'extraction.
   *
   * **La proposition machine n'est JAMAIS réécrite.** Après cet appel,
   * `lirePlan` rend la même `valeur_document` et renseigne `decision` : ce
   * que la machine a proposé et ce que l'humain a retenu restent tous les
   * deux lisibles, et l'écran montre les deux.
   */
  deciderProposition: (proposalId: string, body: DecisionDeProposition) =>
    request<DecisionEnregistree>(`/extraction-proposals/${proposalId}/decisions`, {
      method: 'POST',
      body,
    }),

  boqs: (projectId: string) => request<Boq[]>(`/projects/${projectId}/boqs`),
  createBoq: (projectId: string, body: Record<string, unknown>) =>
    request<Boq>(`/projects/${projectId}/boqs`, { method: 'POST', body }),
  boqItems: (boqId: string) => request<BoqItem[]>(`/boqs/${boqId}/items`),
  updateBoqItem: (itemId: string, body: Record<string, unknown>) =>
    request<BoqItem>(`/boq-items/${itemId}`, {
      method: 'PATCH',
      body,
    }),
  createBoqItem: (boqId: string, body: Record<string, unknown>) =>
    request<BoqItem>(`/boqs/${boqId}/items`, { method: 'POST', body }),

  priceBooks: () => request<PriceBook[]>('/price-books'),
  createPriceBook: (body: Record<string, unknown>) =>
    request<PriceBook>('/price-books', { method: 'POST', body }),
  createPriceItem: (versionId: string, body: Record<string, unknown>) =>
    request<PriceItem>(`/price-books/versions/${versionId}/items`, { method: 'POST', body }),
  priceBookVersions: (bookId: string) =>
    request<PriceBookVersion[]>(`/price-books/${bookId}/versions`),
  publishPriceBookVersion: (versionId: string) =>
    request<PriceBookVersion>(`/price-books/versions/${versionId}/publish`, { method: 'POST' }),
  // `label` voyage en paramètre de requête, pas dans le corps : c'est ainsi que
  // la route le déclare, et l'envoyer dans un corps le ferait ignorer en
  // silence — la version naîtrait sans nom.
  createPriceBookVersion: (bookId: string, label: string) =>
    request<PriceBookVersion>(
      `/price-books/${bookId}/versions?label=${encodeURIComponent(label)}`,
      { method: 'POST' },
    ),
  priceItems: (versionId: string, query = '') =>
    request<Page<PriceItem>>(`/price-books/versions/${versionId}/items${query}`),
  composites: (versionId: string) =>
    request<CompositePrice[]>(`/price-books/versions/${versionId}/composites`),
  composite: (compositeId: string) =>
    request<CompositePrice>(`/price-books/composites/${compositeId}`),
  createComposite: (versionId: string, body: CompositeInput) =>
    request<CompositePrice>(`/price-books/versions/${versionId}/composites`, {
      method: 'POST',
      body,
    }),
  updateComposite: (compositeId: string, body: CompositeInput & { revision: number }) =>
    request<CompositePrice>(`/price-books/composites/${compositeId}`, {
      method: 'PUT',
      body,
    }),
  duplicateComposite: (compositeId: string, body: { code: string; label?: string }) =>
    request<CompositePrice>(`/price-books/composites/${compositeId}/duplicate`, {
      method: 'POST',
      body,
    }),
  deleteComposite: (compositeId: string) =>
    request<void>(`/price-books/composites/${compositeId}`, { method: 'DELETE' }),
  previewComposite: (versionId: string, body: { unit_code: string; components: Composant[] }) =>
    request<CompositePreview>(`/price-books/versions/${versionId}/composites/preview`, {
      method: 'POST',
      body,
    }),
  /**
   * Prévisualise un fichier de prix — CSV ou classeur — sans rien écrire.
   *
   * `feuille` ne vaut que pour un classeur, et reste facultative : sans elle
   * le serveur lit la première ET dit laquelle, avec la liste des autres, pour
   * que l'écran propose de changer plutôt que de laisser croire au vide.
   */
  previewImport: (versionId: string, file: File, feuille?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (feuille) form.append('feuille', feuille)
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

  createEstimate: (body: Record<string, unknown>) =>
    request<Estimate>('/estimates', { method: 'POST', body }),

  estimates: (projectId?: string) =>
    request<Estimate[]>(`/estimates${projectId ? `?project_id=${projectId}` : ''}`),
  estimate: (id: string) => request<Estimate>(`/estimates/${id}`),
  estimateVersions: (id: string) => request<EstimateVersion[]>(`/estimates/${id}/versions`),
  /**
   * Une version de plus sur la même estimation.
   *
   * C'est le SEUL moyen de corriger un chiffrage déjà gelé — et, une fois le
   * devis émis, de le corriger sans réécrire ce qui a été remis. Les lignes
   * viennent du bordereau, pas d'une copie : la nouvelle version chiffre
   * l'état courant du métré.
   */
  createEstimateVersion: (estimateId: string, label?: string) =>
    request<EstimateVersion>(`/estimates/${estimateId}/versions`, {
      method: 'POST',
      body: { label: label ?? null },
    }),
  computation: (estimateId: string, versionId: string) =>
    request<Computation>(`/estimates/${estimateId}/versions/${versionId}/computation`),
  /**
   * Simule trois scénarios. POST parce qu'un corps est nécessaire, mais
   * n'écrit RIEN : le registre transactionnel la classe en lecture.
   */
  scenarios: (
    estimateId: string,
    versionId: string,
    body: Record<string, unknown>,
  ) =>
    request<ScenariosSimulation>(`/estimates/${estimateId}/versions/${versionId}/scenarios`, {
      method: 'POST',
      body,
    }),
  freeze: (estimateId: string, versionId: string, label?: string) =>
    request<EstimateVersion>(`/estimates/${estimateId}/versions/${versionId}/freeze`, {
      method: 'POST',
      body: { confirm: true, label: label ?? null },
    }),
  issueQuote: (estimateId: string, versionId: string, body: Record<string, unknown>) =>
    request<IssuedQuote>(`/estimates/${estimateId}/versions/${versionId}/issue`, {
      method: 'POST',
      body,
    }),
  issuedQuotes: (projectId: string) =>
    request<IssuedQuote[]>(`/projects/${projectId}/issued-quotes`),

  quotes: (query = '') => request<QuoteBoardPage>(`/quotes${query}`),
  quote: (id: string) => request<IssuedQuoteDetail>(`/issued-quotes/${id}`),
  createShareLink: (id: string, days?: number) =>
    request<ShareLinkCreated>(`/issued-quotes/${id}/share-links`, {
      method: 'POST',
      body: { days: days ?? null },
    }),
  revokeShareLink: (id: string, linkId: string) =>
    request<void>(`/issued-quotes/${id}/share-links/${linkId}`, { method: 'DELETE' }),
  recordQuoteEvent: (id: string, body: Record<string, unknown>) =>
    request<IssuedQuoteDetail>(`/issued-quotes/${id}/events`, { method: 'POST', body }),
  correctQuoteEvent: (id: string, eventId: string, body: Record<string, unknown>) =>
    request<IssuedQuoteDetail>(`/issued-quotes/${id}/events/${eventId}/correction`, {
      method: 'POST',
      body,
    }),

  /**
   * Le côté public : aucun jeton porteur, un cookie `HttpOnly` posé par le
   * serveur. `credentials: 'include'` est indispensable — l'API vit sur une
   * autre origine que la page en développement, et sans lui le cookie ne
   * repartirait jamais.
   */
  publicOpenSession: (secret: string) =>
    publicRequest<void>('/public/quote-sessions', { method: 'POST', body: { secret } }),
  publicQuote: () => publicRequest<PublicQuoteView>('/public/quote'),
  publicRespond: (body: Record<string, unknown>) =>
    publicRequest<PublicReceipt>('/public/quote/response', { method: 'POST', body }),
  publicPdfUrl: () => `${API_URL}/public/quote/document.pdf`,
  /**
   * Le logo FIGÉ par ce devis, servi par le cookie de session publique.
   *
   * Une balise `<img>` convient ici, contrairement au PDF : le cookie repart
   * seul avec la requête de l'image, là où le PDF exige un en-tête que seul
   * `fetch` peut poser.
   */
  publicLogoUrl: () => `${API_URL}/public/quote/logo`,
  /**
   * L'URL du PDF remis — à passer par `fetchExport`, jamais à un `<a href>`.
   *
   * La route exige le jeton porteur, et le jeton vit dans `sessionStorage` :
   * un lien nu partirait sans en-tête et rapporterait un 401 que l'écran ne
   * saurait pas expliquer.
   */
  issuedQuoteUrl: (quoteId: string) => `${API_URL}/issued-quotes/${quoteId}/document.pdf`,

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
  login_methods: ('dev' | 'oidc')[]
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

export type Logo = {
  sha256: string
  byte_size: number
  media_type: string
  width: number
  height: number
  updated_at: string | null
}

export type Organization = {
  id: string
  name: string
  legal_name: string | null
  company_number: string | null
  country_code: string
  region_code: string
  currency: string
  address: string | null
  address_complement: string | null
  postal_code: string | null
  city: string | null
  email: string | null
  phone: string | null
  website: string | null
  logo: Logo | null
  /** Ce qui manque pour émettre un NOUVEAU devis. Calculé par le serveur. */
  missing_for_issue: string[]
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
  quote_number_pattern: string
  /** Le numéro que ce motif produirait, rendu par le serveur. */
  quote_number_preview: string
  show_internal_costs_in_client_pdf: boolean
}

export type QuoteNumberPreview = {
  valid: boolean
  preview: string | null
  message: string | null
}

export type Client = {
  id: string
  name: string
  company_number: string | null
  billing_address: string | null
  postal_code: string | null
  city: string | null
  country_code: string
  contact_name: string | null
  email: string | null
  phone: string | null
  notes: string | null
  status: string
}

/** Ce qu'il faut à une fiche pour qu'un devis lui soit adressable. */
export const CHAMPS_POUR_EMETTRE = ['name', 'billing_address', 'postal_code', 'city'] as const

export function manqueAuClient(fiche: Client | null | undefined): string[] {
  if (!fiche) return [...CHAMPS_POUR_EMETTRE]
  return CHAMPS_POUR_EMETTRE.filter((champ) => !(fiche[champ] ?? '').trim())
}

export type IssuedQuote = {
  id: string
  number: string
  project_id: string
  estimate_id: string
  estimate_version_id: string
  client_id: string
  client_name: string
  issued_at: string
  valid_until: string
  terms: string | null
  include_internal_costs: boolean
  pdf_sha256: string
  pdf_byte_size: number
  version_number: number
  issued_by_email: string | null
}

export type Project = {
  id: string
  reference: string
  client_reference: string | null
  name: string
  /** La fiche du répertoire, quand le chantier en a une. */
  client_id: string | null
  /** Le nom libre d'avant le répertoire. Conservé, jamais converti d'office. */
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

export type DocumentSummary = {
  id: string
  project_id: string
  title: string
  status: string
  created_at: string
  updated_at: string
}

export type DocumentRevision = {
  id: string
  document_id: string
  revision_number: number
  sha256: string
  byte_size: number
  media_type: string
  original_filename: string
  author_email: string | null
  status: string
  published_at: string | null
  created_at: string
}

// --- lecture d'un plan DXF ------------------------------------------------
//
// Les décimaux voyagent en CHAÎNES, comme partout ailleurs dans cette API :
// une mesure lue sur un plan ne doit pas perdre un chiffre en passant par un
// flottant du navigateur. Rien n'est converti ici — ni unité, ni échelle, ni
// total : l'écran affiche ce que le serveur dit.

/** Une réserve ou une anomalie, DÉJÀ rédigée en français par le serveur. */
export interface PlanAnomalie {
  code: string
  message: string
}

/**
 * Où se situe un objet dans l'image rendue.
 *
 * Déjà normalisé dans [0,1] par le serveur, **origine en haut à gauche** —
 * donc dans le même sens qu'un positionnement CSS, sans inversion d'axe.
 */
export interface PlanCadre {
  x0: string
  y0: string
  x1: string
  y1: string
}

export interface PlanMesure {
  proposal_id: string
  citation_id: string
  /** Le décimal, en chaîne, dans l'unité DU DOCUMENT. Jamais converti. */
  valeur_document: string
  /** `'mm' | 'cm' | 'm' | 'km' | 'in' | 'ft'`, ou `null` si le plan n'en déclare aucune. */
  unite_document: string | null
  /**
   * `'lineaire' | 'alignee' | 'diametre' | 'rayon' | 'angulaire'
   *  | 'angulaire_3_points' | 'ordonnee' | 'inconnue'`
   *
   * Laissé en `string` : la liste appartient au serveur et peut s'allonger.
   * Un code inconnu de l'écran s'affiche tel quel plutôt qu'en « — ».
   */
  famille: string
  fiabilite: 'mesurable' | 'a_confirmer'
  origine_de_la_mesure: 'cote_42' | 'recalcul'
  /** Ce que le dessinateur a tapé À LA PLACE de la mesure. Une divergence. */
  texte_impose: string | null
  /** PLUSIEURS réserves possibles sur une même mesure. */
  reserves: PlanAnomalie[]
  /** Décimal en chaîne, dans [0,1]. */
  confiance: string
  calque: string | null
  feuille: string | null
  /** Le handle DXF : la désignation stable de l'objet dans le fichier. */
  object_ref: string | null
  cadre: PlanCadre | null
  /** `null` tant qu'aucun humain ne s'est prononcé. */
  decision: 'accepted' | 'corrected' | 'rejected' | null
  /** La valeur retenue par l'humain, s'il a corrigé. */
  valeur_corrigee: string | null
}

export interface PlanLu {
  revision_id: string
  /** `false` quand rien ne peut être mesuré — typiquement sans unité source. */
  mesurable: boolean
  unite_source: string | null
  insunits: number | null
  version_dxf: string | null
  feuilles: string[]
  /** Nombre d'entités par calque. */
  calques: Record<string, number>
  /** Nombre d'entités par type. */
  entites: Record<string, number>
  refuse: boolean
  motif_du_refus: PlanAnomalie | null
  anomalies: PlanAnomalie[]
  image_disponible: boolean
  mesures: PlanMesure[]

  /** `dxf` ou `pdf`. À lire AVANT le reste : les champs de l'autre format
   * valent `null` ou une liste vide, et un écran qui l'ignorerait afficherait
   * « sans unité » pour un PDF — ce qui est vrai, et trompeur. */
  format: 'dxf' | 'pdf'
  /** Zéro pour un DXF. */
  pages: number
  /** Largeur et hauteur de chaque page, en points PostScript. */
  dimensions_des_pages: number[][]
  /** Faux pour un document scanné : l'aperçu sert, l'extraction non. */
  porte_du_texte: boolean
  fragments_lus: number
  /** Les FAITS de l'extraction, affichés tels quels plutôt qu'un verdict. */
  caracteres_extraits: number
  traces_vectoriels: number
  images_incluses: number
  /** Vrai seulement si : aucun texte, aucun tracé, et au moins une image. */
  probablement_scanne: boolean
  /** Les pages qui ont un aperçu. Une absente n'est pas affichable. */
  apercus: number[]
}

/**
 * Une tuile servie, avec ce qu'il faut pour placer un clic dessus.
 *
 * `zone` est ce que l'image couvre VRAIMENT, et non ce qui a été demandé.
 * `zoneDeclaree` dit si le serveur l'a fournie : sinon on retombe sur la
 * demande, et l'écran le signale plutôt que de laisser un décalage sans cause.
 */
export type TuileDePlan = {
  blob: Blob
  zone: [number, number, number, number]
  zoneDeclaree: boolean
  pixels: [number, number] | null
  depuisLeCache: boolean
}

/** Un point désigné sur l'aperçu : [0,1], origine en haut à gauche. */
export type PointDEcran = { x: number; y: number }

/** Un fragment de texte d'un PDF, et où il se trouve. */
export type FragmentDeTexte = {
  texte: string
  page: number
  /** `null` quand la position n'a pas pu être établie — voir `position`. */
  cadre: PlanCadre | null
  /** `exacte`, `recadree` ou `inconnue`. */
  position: string
}

export type TextesDePlan = {
  revision_id: string
  /** Le total de la SÉLECTION, pas de la tranche rendue. */
  total: number
  page: number | null
  fragments: FragmentDeTexte[]
  extracteur: string
}

/** Une échelle déclarée par une personne sur une page de PDF. */
export type CalibrationDePlan = {
  id: string
  page: number
  distance_reelle: string
  unite: string
  /** « 50 mm par point ». À LIRE, jamais à recalculer. */
  facteur_lisible: string
  resolution_du_pointage: string
  motif: string
  zone: string[] | null
  created_at: string
}

/** Une mesure prise sur un PDF, avec de quoi la juger et la retrouver. */
export type MesureDePdf = {
  proposal_id: string
  citation_id: string
  page: number
  type: string
  libelle: string
  valeur: string
  unite: string
  /** Dans la MÊME unité que la valeur. */
  incertitude: string
  incertitude_relative: string
  fiabilite: string
  reserves: string[]
  points: PointDEcran[]
  cadre: PlanCadre | null
  calibration: Record<string, unknown>
  decision: string | null
  /** Pourquoi la personne a tranché ainsi. Une décision sans sa raison n'est
      pas auditable : dans six mois, « 3,80 m » ne vaut que si l'on sait d'où
      ce nombre vient. */
  motif_de_la_decision: string | null
  valeur_corrigee: string | null
  /**
   * Les mêmes nombres, écrits pour être LUS — « 4 181 mm », « ± 26 mm ».
   *
   * Rendus par le SERVEUR, et l'écran ne refait pas l'arrondi : deux règles
   * d'affichage, une en Python et une ici, finiraient par diverger d'un
   * chiffre, et c'est l'écart qu'on ne voit jamais venir. La valeur exacte
   * reste au-dessus, et c'est elle qu'on reprendrait pour calculer.
   */
  valeur_lisible: string
  incertitude_lisible: string
  valeur_retenue_lisible: string | null
  unite_retenue: string | null
  /** Ce qui compte une fois la décision prise : la valeur retenue si la
      personne a corrigé, la mesure si elle a confirmé, `null` si elle a rejeté
      ou n'a pas encore tranché. */
  valeur_retenue: string | null
  /** Vrai quand cette mesure peut alimenter un bordereau. Faux pour une mesure
      rejetée, et faux tant que personne n'a tranché. */
  reprenable: boolean
}

export type MesuresDePdf = {
  revision_id: string
  calibrations: CalibrationDePlan[]
  mesures: MesureDePdf[]
}

export type DecisionHumaine = 'accepted' | 'corrected' | 'rejected'

/**
 * Le corps d'une décision humaine — union DISCRIMINÉE, pas quatre champs
 * facultatifs.
 *
 * **Règle du serveur, déjà testée :** `before_value` et `after_value` sont
 * OBLIGATOIRES pour `corrected` et INTERDITS pour les deux autres. Un type
 * permissif laisserait écrire ici un corps que le serveur refuse par un 422,
 * et le refus n'arriverait qu'au navigateur de l'utilisateur.
 */
export type DecisionDeProposition =
  | { decision: 'accepted' | 'rejected'; reason: string }
  | {
      decision: 'corrected'
      reason: string
      before_value: Record<string, string>
      after_value: Record<string, string>
    }

/** L'accusé de décision : il ne répète pas les valeurs documentaires. */
export type DecisionEnregistree = {
  id: string
  proposal_id: string
  actor_user_id: string
  decision: DecisionHumaine
  created_at: string
}

export type Member = {
  id: string
  user_id: string
  email: string
  full_name: string
  role: string
  role_label: string
  is_active: boolean
}

export type TaxRate = {
  id: string
  code: string
  label: string
  rate: string
  applies_from: string | null
  applies_to: string | null
  is_default: boolean
  source: string | null
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

/**
 * Un sous-détail de prix, tel que l'API le rend.
 *
 * `components` reste volontairement générique : chaque type de composant
 * (`consumption`, `output_rate`, `rotation`, `lump_sum`) porte des champs
 * différents, et le serveur les sérialise en chaînes pour que le décimal
 * saisi survive au transport. Recopier ces quatre formes ici donnerait deux
 * vérités à tenir d'accord ; l'écran lit ce qui est présent.
 */
/** Les quatre types de composants, dans le vocabulaire du serveur. */
export const TYPES_DE_COMPOSANT = ['consumption', 'output_rate', 'rotation', 'lump_sum'] as const
export type TypeDeComposant = (typeof TYPES_DE_COMPOSANT)[number]

/**
 * Un composant en cours de saisie.
 *
 * Volontairement permissif : chaque type porte des champs différents, et le
 * formulaire n'envoie que ceux qui s'appliquent. Recopier ici les quatre
 * formes exactes du serveur donnerait deux vérités à tenir d'accord — et
 * c'est le serveur qui valide, champ par champ, avec l'index du composant
 * fautif.
 */
export type Composant = {
  component_type: TypeDeComposant
  label: string
  resource_kind: string
  [champ: string]: unknown
}

export type CompositeInput = {
  code: string
  label: string
  unit_code: string
  notes?: string | null
  components: Composant[]
}

export type CompositePrice = {
  id: string
  code: string
  label: string
  unit_code: string
  notes: string | null
  is_demo_data: boolean
  /** Le jeton de concurrence : à renvoyer tel quel dans une modification. */
  revision: number
  /** Version publiée : le sous-détail est en lecture seule. */
  version_published: boolean
  /** Combien de postes s'en servent. Au-delà de zéro, la suppression est refusée. */
  referenced_by: number
  components: Composant[]
}

export type CompositePreview = {
  unit_code: string
  currency: string
  /** `false` quand un composant à rotations arrondies rend le coût non proportionnel. */
  scales_linearly: boolean
  /** Le décimal exact, non arrondi. */
  unit_cost: string
  /** Le même, arrondi par le SERVEUR. C'est celui qu'on affiche. */
  unit_cost_display: string
  by_kind: { resource_kind: string; label: string; amount: string; amount_display: string }[]
  components: Record<string, unknown>[]
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
    encoding: string | null
    unmapped_headers: string[]
    missing_required_columns: string[]
    fatal: string | null
    /** « csv » ou « xlsx » : ce que le serveur a DÉTECTÉ, pas ce que le nom disait. */
    format?: string
    /** Classeur seulement : la feuille lue, et toutes celles qu'il porte. */
    feuille?: string
    feuilles?: string[]
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

/**
 * Une simulation de scénarios. Rien n'a été écrit pour la produire.
 *
 * Les hypothèses sont des ÉCARTS RELATIFS sérialisés en décimal : `"0.1"` vaut
 * « +10 % ». L'écran saisit et affiche des pourcentages humains ; la
 * conversion est un décalage de virgule sur la chaîne, jamais une division en
 * virgule flottante — voir `enFraction` dans le panneau.
 */
export type ScenarioHypotheses = {
  prix: string
  prix_categories: string[]
  productivite: string
  distance: string
}

export type ScenarioEcart = {
  /** Le décimal EXACT, pour qui recalcule. Périodique sur un écart de productivité. */
  absolu: string
  /** Le même, arrondi PAR LE SERVEUR. C'est celui que l'écran affiche. */
  absolu_display: string
  /** `null` quand la référence vaut zéro : une division par zéro n'a pas de résultat. */
  pourcentage: string | null
}

export type ScenarioCalcule = {
  status: 'success'
  nom: string
  hypotheses: ScenarioHypotheses
  totaux: EstimateResult
  lignes_sans_prix: string[]
  bloquant: boolean
  ecart: ScenarioEcart | null
}

export type ScenarioRefuse = {
  status: 'refused'
  nom: string
  hypotheses: ScenarioHypotheses
  refus: { code?: string; message?: string; scenario?: string }
}

/**
 * Union DISCRIMINÉE sur `status`, comme l'OpenAPI la décrit : un scénario
 * porte ses totaux OU son refus, jamais les deux et jamais aucun des deux.
 * L'écran n'a donc pas à deviner en testant la présence d'une clé.
 */
export type Scenario = ScenarioCalcule | ScenarioRefuse

export type ScenariosSimulation = {
  version: EstimateVersion
  computed_at: string
  from_snapshot: boolean
  /** `cost:read` : déboursés, coûts et ressources. */
  includes_internal_costs: boolean
  /** `margin:read` : étapes de markup, avec leurs taux. Séparé des coûts. */
  includes_margin_steps: boolean
  currency: string
  /** Dans l'ordre bas / probable / haut — jamais réordonné. */
  scenarios: Scenario[]
  ordre_incoherent: boolean
  /** Les natures de ressource et leur libellé, RENDUES PAR LE SERVEUR. */
  categories: Record<string, string>
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

export type QuoteState = {
  code: string
  label: string
  decision: string | null
  transmitted_at: string | null
  viewed_at: string | null
  decided_at: string | null
  last_activity_at: string | null
  expired: boolean
}

export type QuoteEvent = {
  id: string
  kind: string
  kind_label: string
  channel: string | null
  actor_email: string | null
  respondent_name: string | null
  respondent_email: string | null
  comment: string | null
  effective_at: string
  recorded_at: string
  corrected: boolean
  correction_reason: string | null
  corrects_event_id: string | null
}

export type ShareLink = {
  id: string
  created_at: string
  expires_at: string
  revoked_at: string | null
  active: boolean
}

/** La seule réponse qui porte le secret, et une seule fois. */
export type ShareLinkCreated = { link: ShareLink; url: string }

export type QuoteBoardRow = {
  id: string
  number: string
  client_name: string
  project_id: string
  project_reference: string
  project_name: string
  total_ttc: string
  currency: string
  issued_at: string
  valid_until: string
  state: QuoteState
  has_active_link: boolean
}

export type QuoteBoardPage = { items: QuoteBoardRow[]; page: Page<never>['page'] }

export type IssuedQuoteDetail = {
  quote: IssuedQuote
  state: QuoteState
  events: QuoteEvent[]
  links: ShareLink[]
  project_reference: string
  project_name: string
  client_snapshot: Record<string, string | null>
  total_ttc: string
  currency: string
}

export type PublicQuoteLine = {
  position: string
  designation: string
  unit: string
  quantity: string
  unit_price_ht: string
  total_ht: string
}

export type PublicQuoteView = {
  number: string
  issued_at: string
  valid_until: string
  organization_name: string
  organization_legal_name: string | null
  organization_company_number: string | null
  /** L'adresse de l'émetteur, telle qu'elle était à l'émission. */
  organization_address_lines: string[]
  organization_email: string | null
  organization_phone: string | null
  organization_website: string | null
  /** Vrai quand ce devis a figé un logo — servi par sa propre route. */
  has_logo: boolean
  client_name: string
  client_address_lines: string[]
  project_reference: string
  project_name: string
  lines: PublicQuoteLine[]
  total_ht: string
  taxes: { code?: string; label: string; rate: string; amount: string }[]
  total_ttc: string
  currency: string
  terms: string | null
  pdf_sha256: string
  pdf_byte_size: number
  state: QuoteState
  can_respond: boolean
  cannot_respond_reason: string | null
}

export type PublicReceipt = {
  number: string
  decision: string
  decision_label: string
  decided_at: string
  respondent_name: string | null
  pdf_sha256: string
  created: boolean
}

export type AuditVerify = {
  valid: boolean
  checked: number
  head_hash: string | null
  failed_at_sequence: number | null
  reason: string | null
}
