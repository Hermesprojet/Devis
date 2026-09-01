'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { BibliothequeVide } from '@/components/BibliothequeVide'
import { ErrorNotice, Loading } from '@/components/Feedback'
import { NouveauPrix } from '@/components/NouveauPrix'
import { SousDetails } from '@/components/SousDetails'
import { Shell } from '@/components/Shell'
import {
  api,
  type ImportOutcome,
  type ImportReport,
  type PriceBook,
  type PriceBookVersion,
  type PriceItem,
} from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

const STRATEGIES = ['create', 'replace', 'ignore', 'merge'] as const

export default function PriceBookPage() {
  const [books, setBooks] = useState<PriceBook[]>([])
  const [versions, setVersions] = useState<PriceBookVersion[]>([])
  const [versionId, setVersionId] = useState<string>('')
  const [items, setItems] = useState<PriceItem[]>([])
  const [search, setSearch] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [ready, setReady] = useState(false)

  const [report, setReport] = useState<ImportReport | null>(null)
  const [strategy, setStrategy] = useState<(typeof STRATEGIES)[number]>('create')
  const [outcome, setOutcome] = useState<ImportOutcome | null>(null)
  const [publication, setPublication] = useState(false)
  // Le fichier est GARDÉ après la première lecture : changer de feuille doit
  // relire le même classeur, et redemander le fichier à l'utilisateur pour un
  // choix qu'on vient de lui proposer serait absurde.
  const [classeur, setClasseur] = useState<File | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const permissions = usePermissions()

  const boot = useCallback(async () => {
    try {
      const loadedBooks = await api.priceBooks()
      setBooks(loadedBooks)
      const first = loadedBooks[0]
      if (first) {
        const loadedVersions = await api.priceBookVersions(first.id)
        setVersions(loadedVersions)
        const firstVersion = loadedVersions[0]
        if (firstVersion) setVersionId(firstVersion.id)
      }
    } catch (caught) {
      setError(caught)
    } finally {
      setReady(true)
    }
  }, [])

  useEffect(() => {
    void boot()
  }, [boot])

  const loadItems = useCallback(
    async (query: string) => {
      if (!versionId) return
      try {
        const page = await api.priceItems(
          versionId,
          query ? `?q=${encodeURIComponent(query)}&limit=200` : '?limit=200',
        )
        setItems(page.items)
      } catch (caught) {
        setError(caught)
      }
    },
    [versionId],
  )

  useEffect(() => {
    void loadItems('')
  }, [loadItems])

  /**
   * Publier : irréversible, donc confirmé, et jamais offert sans le droit.
   *
   * Le bouton n'existait pas. Publier une version se faisait par appel d'API,
   * ce qui laissait l'écran des sous-détails annoncer une lecture seule que
   * personne ne pouvait déclencher depuis le navigateur.
   */
  /**
   * Créer une nouvelle version.
   *
   * Le bandeau de publication dit « créez une nouvelle version pour les faire
   * évoluer ». Il le disait sans qu'aucun bouton ne le permette : depuis le
   * navigateur, publier était une impasse.
   */
  async function nouvelleVersion() {
    const premier = books[0]
    if (!premier) return
    const nom = window.prompt(t('priceBook.newVersionPrompt'), `v${versions.length + 1}`)
    if (!nom) return
    try {
      const creee = await api.createPriceBookVersion(premier.id, nom)
      setVersions([creee, ...versions])
      setVersionId(creee.id)
      setError(null)
    } catch (caught) {
      setError(caught)
    }
  }

  async function publier() {
    if (!versionId) return
    try {
      const publiee = await api.publishPriceBookVersion(versionId)
      setVersions((liste) => liste.map((v) => (v.id === publiee.id ? publiee : v)))
      setPublication(false)
      setError(null)
    } catch (caught) {
      setError(caught)
    }
  }

  async function preview(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file || !versionId) return
    setClasseur(file)
    await lire(file)
  }

  /** Lit le fichier retenu, éventuellement sur une autre feuille. */
  async function lire(file: File, feuille?: string) {
    if (!versionId) return
    setError(null)
    setOutcome(null)
    try {
      setReport(await api.previewImport(versionId, file, feuille))
    } catch (caught) {
      // Le rapport précédent est effacé : le garder afficherait le résultat
      // d'une feuille pendant qu'un message parle d'une autre.
      setReport(null)
      setError(caught)
    }
  }

  async function commit() {
    if (!report) return
    setError(null)
    try {
      setOutcome(await api.commitImport(report.batch_id, strategy))
      setReport(null)
      setClasseur(null)
      if (fileInput.current) fileInput.current.value = ''
      await loadItems(search)
    } catch (caught) {
      setError(caught)
    }
  }

  if (!ready) {
    return (
      <Shell>
        <Loading />
      </Shell>
    )
  }

  if (books.length === 0) {
    return (
      <Shell>
        <h1>{t('priceBook.title')}</h1>
        <ErrorNotice error={error} />
        <BibliothequeVide onCree={() => void boot()} />
      </Shell>
    )
  }

  const versionSelectionnee = versions.find((version) => version.id === versionId)
  const figee = versionSelectionnee?.status === 'published'
  // Une version publiée ne se republie pas : l'API répondrait 409, et offrir
  // une commande qui échoue est précisément ce qu'on cherche à éviter.
  const peutEcrire = can(permissions, PERMISSIONS.pricebookWrite)
  const peutPublier = Boolean(versionId) && !figee && peutEcrire

  return (
    <Shell>
      <h1>{t('priceBook.title')}</h1>
      <ErrorNotice error={error} />

      <div className="toolbar">
        <select
          style={{ maxWidth: 320 }}
          value={versionId}
          onChange={(event) => setVersionId(event.target.value)}
          aria-label="Version de bibliothèque"
        >
          {versions.map((version) => (
            <option key={version.id} value={version.id}>
              {books[0]?.name} — v{version.version_number} ({version.status})
            </option>
          ))}
        </select>
        <input
          style={{ maxWidth: 280 }}
          placeholder={t('common.search')}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value)
            void loadItems(event.target.value)
          }}
        />
        <div className="spacer" />
        {versionSelectionnee?.status === 'published' && (
          <span className="badge" data-testid="version-publiee-badge">
            {t('priceBook.published')}
          </span>
        )}
        {peutPublier && (
          <button onClick={() => setPublication(true)}>{t('priceBook.publish')}</button>
        )}
        {peutEcrire && (
          <button onClick={() => void nouvelleVersion()}>{t('priceBook.newVersion')}</button>
        )}
        {versionId && !figee && (
          <NouveauPrix versionId={versionId} onCree={() => void loadItems(search)} />
        )}
        <a className="button" href="/modele_import_prix.csv" download>
          Modèle CSV
        </a>
      </div>

      {publication && (
        <div className="card" data-testid="confirmation-publication">
          <div className="notice warning" role="alert">
            {t('priceBook.publishWarning')}
          </div>
          <button className="primary" onClick={() => void publier()}>
            {t('priceBook.publishConfirm')}
          </button>{' '}
          <button onClick={() => setPublication(false)}>{t('common.cancel')}</button>
        </div>
      )}

      {figee && (
        <div className="notice info" data-testid="bibliotheque-figee">
          {t('composites.publishedReadOnly')}
        </div>
      )}

      {!figee && (
      <div className="card">
        <h2 style={{ marginTop: 0 }}>{t('import.title')}</h2>
        <div className="notice info">{t('import.nothingWritten')}</div>

        <div className="field">
          <label htmlFor="file">{t('import.step1')}</label>
          <input
            id="file"
            ref={fileInput}
            type="file"
            // Le serveur décide du format sur le CONTENU ; cette liste ne fait
            // qu'aider le sélecteur de fichiers, elle ne décide de rien.
            accept=".csv,text/csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={preview}
          />
          <p className="muted">{t('import.formats')}</p>
        </div>

        {report?.meta.feuilles && report.meta.feuilles.length > 1 && (
          <div className="field" data-testid="choix-de-feuille">
            <label htmlFor="feuille">{t('import.sheet')}</label>
            <select
              id="feuille"
              value={report.meta.feuille ?? ''}
              onChange={(evenement) => {
                if (classeur) void lire(classeur, evenement.target.value)
              }}
            >
              {report.meta.feuilles.map((nom) => (
                <option key={nom} value={nom}>
                  {nom}
                </option>
              ))}
            </select>
            <p className="muted">{t('import.sheetHint')}</p>
          </div>
        )}

        {outcome && (
          <div className="notice success">
            {t('import.committed')} — {outcome.created} créé(s), {outcome.updated} mis à jour,{' '}
            {outcome.skipped} ignoré(s), {outcome.conflicted} conflit(s).
          </div>
        )}

        {report && <ImportPreview report={report} />}

        {report && report.meta.fatal === null && (
          <>
            <h3>{t('import.step3')}</h3>
            <div className="row" style={{ marginTop: 12 }}>
              <div className="field">
                <label htmlFor="strategy">{t('import.strategy')}</label>
                <select
                  id="strategy"
                  value={strategy}
                  onChange={(event) =>
                    setStrategy(event.target.value as (typeof STRATEGIES)[number])
                  }
                >
                  {STRATEGIES.map((option) => (
                    <option key={option} value={option}>
                      {t(`import.strategy.${option}`)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <button className="primary" onClick={() => void commit()} disabled={report.valid_count === 0}>
              {t('import.commit')} ({report.valid_count} {t('import.valid')})
            </button>
          </>
        )}
      </div>
      )}

      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>{t('priceBook.code')}</th>
              <th>{t('priceBook.label')}</th>
              <th>{t('priceBook.family')}</th>
              <th>{t('common.unit')}</th>
              <th className="num">{t('priceBook.unitPrice')}</th>
              <th>{t('priceBook.supplier')}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td className="mono">{item.code}</td>
                <td>
                  {item.label}
                  {item.is_demo_data && (
                    <>
                      {' '}
                      <span className="badge warning">{t('priceBook.demoFlag')}</span>
                    </>
                  )}
                </td>
                <td>{item.family ?? t('common.none')}</td>
                <td>{item.unit_code}</td>
                <td className="num">
                  {item.unit_price} {item.currency}
                </td>
                <td>{item.supplier_name ?? t('common.none')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <SousDetails versionId={versionId} />
    </Shell>
  )
}

function ImportPreview({ report }: { report: ImportReport }) {
  if (report.meta.fatal) {
    return (
      <div className="notice error">
        {report.meta.fatal === 'empty_file'
          ? 'Le fichier est vide.'
          : `Colonnes obligatoires manquantes : ${report.meta.missing_required_columns.join(', ')}`}
      </div>
    )
  }

  return (
    <>
      <h3>{t('import.step2')}</h3>
      <p className="muted">
        <span className="badge success">
          {report.valid_count} {t('import.valid')}
        </span>{' '}
        <span className={`badge ${report.error_count > 0 ? 'danger' : ''}`}>
          {report.error_count} {t('import.errors')}
        </span>{' '}
        <span className={`badge ${report.duplicate_count > 0 ? 'warning' : ''}`}>
          {report.duplicate_count} {t('import.duplicates')}
        </span>{' '}
        <span className="badge">séparateur « {report.meta.delimiter} »</span>{' '}
        <span className="badge">{report.meta.encoding}</span>
      </p>

      <table>
        <thead>
          <tr>
            <th>{t('import.line')}</th>
            <th>{t('priceBook.code')}</th>
            <th>{t('priceBook.label')}</th>
            <th>{t('common.unit')}</th>
            <th className="num">{t('priceBook.unitPrice')}</th>
            <th>{t('common.status')}</th>
          </tr>
        </thead>
        <tbody>
          {report.rows.map((row) => (
            <tr key={row.line_number}>
              <td className="mono">{row.line_number}</td>
              <td className="mono">{String(row.normalized?.code ?? row.raw.code ?? '')}</td>
              <td>{String(row.normalized?.label ?? row.raw.libelle ?? '')}</td>
              <td>{String(row.normalized?.unit_code ?? '')}</td>
              <td className="num">{String(row.normalized?.unit_price ?? '')}</td>
              <td>
                {row.is_valid ? (
                  row.is_duplicate ? (
                    <span className="badge warning">doublon</span>
                  ) : (
                    <span className="badge success">valide</span>
                  )
                ) : (
                  <ul style={{ margin: 0, paddingLeft: 16 }}>
                    {row.errors.map((issue, index) => (
                      <li key={index} className="mono" style={{ color: 'var(--danger)' }}>
                        {issue.column ? `${issue.column} : ` : ''}
                        {issue.message}
                      </li>
                    ))}
                  </ul>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}
