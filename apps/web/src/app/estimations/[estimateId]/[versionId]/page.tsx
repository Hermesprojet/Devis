'use client'

import { useParams } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'

import { EmissionDuDevis } from '@/components/EmissionDuDevis'
import { ErrorNotice, Loading } from '@/components/Feedback'
import { Shell } from '@/components/Shell'
import { api, type Computation, type EstimateLine } from '@/lib/api'
import { t } from '@/lib/i18n'
import { availability, PERMISSIONS } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * Priced bill of quantities for one estimate version.
 *
 * Every amount on this screen can be opened down to the arithmetic that
 * produced it: a reader who does not trust a figure must be able to find out
 * why it is what it is without leaving the page.
 */
export default function EstimateVersionPage() {
  const params = useParams<{ estimateId: string; versionId: string }>()
  const { estimateId, versionId } = params

  const [computation, setComputation] = useState<Computation | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [ready, setReady] = useState(false)
  const [confirmingFreeze, setConfirmingFreeze] = useState(false)
  const [busy, setBusy] = useState(false)
  const permissions = usePermissions()

  const load = useCallback(async () => {
    setError(null)
    try {
      setComputation(await api.computation(estimateId, versionId))
    } catch (caught) {
      setError(caught)
      setComputation(null)
    } finally {
      setReady(true)
    }
  }, [estimateId, versionId])

  useEffect(() => {
    void load()
  }, [load])

  async function freeze() {
    setBusy(true)
    setError(null)
    try {
      await api.freeze(estimateId, versionId)
      setConfirmingFreeze(false)
      await load()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  /**
   * Exports are authenticated, so a plain <a href> would hit the API without a
   * token. The file is fetched with the bearer token and handed to the browser
   * as a blob.
   *
   * Le `fetch` passe par `api.fetchExport` : cette fonction refaisait le sien
   * et levait une `Error` nue, ce qui jetait le `required_permission` que
   * l'API fournit sur un refus. L'utilisateur lisait « Erreur HTTP 403 » sans
   * savoir quelle permission lui manquait.
   */
  async function download(kind: 'csv' | 'internal' | 'quote') {
    setError(null)
    try {
      const blob = await api.fetchExport(api.exportUrl(estimateId, versionId, kind))
      const url = URL.createObjectURL(blob)
      if (kind === 'quote') {
        window.open(url, '_blank', 'noopener')
      } else {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = `estimation-v${computation?.version.version_number ?? ''}.csv`
        anchor.click()
      }
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
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

  if (!computation) {
    return (
      <Shell>
        <h1>{t('estimate.title')}</h1>
        <ErrorNotice error={error} />
        <button onClick={() => void load()}>{t('common.retry')}</button>
      </Shell>
    )
  }

  const { version, result, includes_internal_costs: internal, from_snapshot: snapshot } = computation
  const frozen = version.status === 'frozen'
  const exportClient = availability(permissions, PERMISSIONS.exportClient)
  const exportInternal = availability(permissions, PERMISSIONS.exportInternal)
  const freezing = availability(
    permissions,
    PERMISSIONS.estimateFreeze,
    frozen ? t('estimate.freezeAlreadyFrozen') : null,
  )

  return (
    <Shell>
      <h1>
        {t('estimate.title')} — {t('estimate.version')} {version.version_number}
      </h1>
      <p className="muted">
        <span className={`badge ${frozen ? 'success' : ''}`}>
          {frozen ? t('estimate.frozen') : t('estimate.draft')}
        </span>{' '}
        {version.label}
        {version.snapshot_sha256 && (
          <>
            {' · '}
            {t('estimate.digest')} <span className="mono">{version.snapshot_sha256.slice(0, 16)}…</span>
          </>
        )}
      </p>

      <ErrorNotice error={error} />
      {snapshot && <div className="notice info">{t('estimate.fromSnapshot')}</div>}
      {result.blocking && <div className="notice warning">{t('estimate.blocked')}</div>}

      {/*
        Deux causes d'indisponibilité, deux traitements. Une commande que le
        rôle ne pourra JAMAIS exécuter est masquée : la montrer désactivée
        n'apprend rien d'actionnable. Une commande autorisée mais bloquée par
        l'état de l'objet reste visible et désactivée, avec l'explication —
        l'utilisateur doit savoir qu'elle existe et pourquoi elle attend.

        L'API reste l'autorité : elle refuse toujours, et c'est la deuxième
        barrière. Ceci évite seulement de proposer ce qui ne peut pas aboutir.
      */}
      <div className="toolbar">
        {exportClient.available && (
          <button onClick={() => void download('csv')}>{t('estimate.exportCsv')}</button>
        )}
        {internal && exportInternal.available && (
          <button onClick={() => void download('internal')}>{t('estimate.exportInternal')}</button>
        )}
        {exportClient.available && (
          <button onClick={() => void download('quote')}>{t('estimate.quotePreview')}</button>
        )}
        <div className="spacer" />
        {freezing.available && (
          <button className="primary" onClick={() => setConfirmingFreeze(true)} disabled={busy}>
            {t('estimate.freeze')}
          </button>
        )}
        {!freezing.available && freezing.reason === 'state' && (
          <button className="primary" disabled title={freezing.explanation}>
            {t('estimate.freeze')}
          </button>
        )}
      </div>
      {!freezing.available && freezing.reason === 'state' && (
        <p className="muted">{freezing.explanation}</p>
      )}

      {confirmingFreeze && (
        <div className="card">
          <div className="notice warning">{t('estimate.freezeWarning')}</div>
          <button className="danger" onClick={() => void freeze()} disabled={busy}>
            {t('common.confirm')}
          </button>{' '}
          <button onClick={() => setConfirmingFreeze(false)}>{t('common.cancel')}</button>
        </div>
      )}

      <EmissionDuDevis estimateId={estimateId} versionId={versionId} frozen={frozen} />

      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>{t('boq.position')}</th>
              <th>{t('boq.designation')}</th>
              <th>{t('common.unit')}</th>
              <th className="num">{t('common.quantity')}</th>
              {internal && <th className="num">{t('estimate.directCost')}</th>}
              <th className="num">{t('estimate.unitPrice')}</th>
              <th className="num">{t('estimate.totalHT')}</th>
            </tr>
          </thead>
          <tbody>
            {result.lines.map((line) => (
              <LineRow key={line.line_id} line={line} internal={internal} currency={result.currency} />
            ))}
          </tbody>
        </table>
      </div>

      <table className="totals">
        <tbody>
          {internal && result.total_direct_cost && (
            <tr>
              <td>{t('estimate.directCost')}</td>
              <td className="num">
                {result.total_direct_cost} {result.currency}
              </td>
            </tr>
          )}
          {internal && result.total_cost_price && (
            <tr>
              <td>{t('estimate.costPrice')}</td>
              <td className="num">
                {result.total_cost_price} {result.currency}
              </td>
            </tr>
          )}
          <tr>
            <td>{t('estimate.totalHT')}</td>
            <td className="num">
              {result.total_selling_price_ht} {result.currency}
            </td>
          </tr>
          {result.taxes.map((tax) => (
            <tr key={tax.code}>
              <td>{tax.label}</td>
              <td className="num">
                {tax.amount} {result.currency}
              </td>
            </tr>
          ))}
          <tr className="grand">
            <td>{t('estimate.totalTTC')}</td>
            <td className="num">
              {result.total_ttc} {result.currency}
            </td>
          </tr>
          {result.options_total_ht !== '0.00' && (
            <tr>
              <td className="muted">{t('estimate.options')}</td>
              <td className="num muted">
                {result.options_total_ht} {result.currency}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </Shell>
  )
}

function LineRow({
  line,
  internal,
  currency,
}: {
  line: EstimateLine
  internal: boolean
  currency: string
}) {
  const isSection = line.kind === 'section'
  const className = isSection ? 'section' : line.included_in_total ? undefined : 'excluded'
  const price = line.price

  return (
    <tr className={className}>
      <td className="mono">{line.code}</td>
      <td>
        {line.designation}
        {line.missing_price && (
          <>
            {' '}
            <span className="badge danger">{t('estimate.missingPrice')}</span>
          </>
        )}
        {!isSection && !line.included_in_total && !line.missing_price && (
          <>
            {' '}
            <span className="badge warning">{t('estimate.options')}</span>
          </>
        )}
        {price?.components && price.components.length > 0 && (
          <details className="subdetail">
            <summary>{t('estimate.showDetail')}</summary>
            <table>
              <thead>
                <tr>
                  <th>{t('estimate.subDetail')}</th>
                  <th className="num">{t('common.quantity')}</th>
                  <th className="num">P.U.</th>
                  <th className="num">{t('common.total')}</th>
                </tr>
              </thead>
              <tbody>
                {price.components.map((component, index) => (
                  <tr key={index}>
                    <td>
                      <strong>{component.label}</strong>
                      <div className="muted" style={{ fontSize: 11 }}>
                        {component.kind_label}
                      </div>
                      <div className="mono" style={{ fontSize: 11 }}>
                        {component.formula}
                      </div>
                      {component.density_source && (
                        <div className="muted" style={{ fontSize: 11 }}>
                          {t('estimate.densitySource')} : {component.density_source}
                        </div>
                      )}
                    </td>
                    <td className="num">
                      {component.resource_quantity} {component.resource_unit}
                    </td>
                    <td className="num">{component.unit_price}</td>
                    <td className="num">{component.amount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {price.markup_steps && (
              <table>
                <thead>
                  <tr>
                    <th>{t('estimate.markupChain')}</th>
                    <th className="num">Taux</th>
                    <th className="num">Montant</th>
                    <th className="num">Cumul</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>{t('estimate.directCost')}</td>
                    <td className="num" />
                    <td className="num" />
                    <td className="num">{price.direct_cost}</td>
                  </tr>
                  {price.markup_steps.map((step) => (
                    <tr key={step.key}>
                      <td>{step.label}</td>
                      <td className="num">{step.rate}</td>
                      <td className="num">{step.amount}</td>
                      <td className="num">{step.running_total}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </details>
        )}
      </td>
      <td>{isSection ? '' : line.unit}</td>
      <td className="num">{isSection ? '' : line.quantity}</td>
      {internal && <td className="num">{price?.direct_cost ?? ''}</td>}
      <td className="num">{price?.unit_price_ht ?? ''}</td>
      <td className="num">
        {price ? `${price.selling_price_ht} ${currency}` : ''}
      </td>
    </tr>
  )
}
