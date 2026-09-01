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
  const fileInput = useRef<HTMLInputElement>(null)

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

  async function preview(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file || !versionId) return
    setError(null)
    setOutcome(null)
    try {
      setReport(await api.previewImport(versionId, file))
    } catch (caught) {
      setError(caught)
    }
  }

  async function commit() {
    if (!report) return
    setError(null)
    try {
      setOutcome(await api.commitImport(report.batch_id, strategy))
      setReport(null)
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
        {versionId && <NouveauPrix versionId={versionId} onCree={() => void loadItems(search)} />}
        <a className="button" href="/modele_import_prix.csv" download>
          Modèle CSV
        </a>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>{t('import.title')}</h2>
        <div className="notice info">{t('import.nothingWritten')}</div>

        <div className="field">
          <label htmlFor="file">{t('import.step1')}</label>
          <input id="file" ref={fileInput} type="file" accept=".csv,text/csv" onChange={preview} />
        </div>

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
