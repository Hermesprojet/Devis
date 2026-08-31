'use client'

import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import {
  api,
  manqueAuClient,
  type Client,
  type IssuedQuote,
  type Project,
} from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

function dateFr(valeur: string): string {
  return new Date(valeur).toLocaleDateString('fr-BE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
}

/**
 * Émettre le devis d'une version gelée, puis le reprendre.
 *
 * Trois refus sont anticipés ici plutôt que subis : version non gelée,
 * chantier sans fiche, version déjà émise. L'API les refuse de toute façon —
 * c'est elle l'autorité — mais un bouton qui ne peut pas aboutir n'apprend
 * rien, et l'utilisateur doit savoir ce qui manque AVANT de cliquer.
 */
export function EmissionDuDevis({
  estimateId,
  versionId,
  frozen,
}: {
  estimateId: string
  versionId: string
  frozen: boolean
}) {
  const [projet, setProjet] = useState<Project | null>(null)
  const [fiche, setFiche] = useState<Client | null>(null)
  const [devis, setDevis] = useState<IssuedQuote[]>([])
  const [erreur, setErreur] = useState<unknown>(null)
  const [ouvert, setOuvert] = useState(false)
  const [occupe, setOccupe] = useState(false)
  const [validite, setValidite] = useState('')
  const [conditions, setConditions] = useState('')
  const [couts, setCouts] = useState(false)
  const permissions = usePermissions()
  const peutEmettre = can(permissions, PERMISSIONS.estimateWrite)
  const peutTelecharger = can(permissions, PERMISSIONS.exportClient)
  const peutCoutsInternes = can(permissions, PERMISSIONS.exportInternal)

  const charger = useCallback(async () => {
    try {
      const estimation = await api.estimate(estimateId)
      const chantier = await api.project(estimation.project_id)
      setProjet(chantier)
      setDevis(await api.issuedQuotes(chantier.id))
      setFiche(chantier.client_id ? await api.client(chantier.client_id) : null)
    } catch (attrape) {
      setErreur(attrape)
    }
  }, [estimateId])

  useEffect(() => {
    void charger()
  }, [charger])

  const celuiCi = devis.find((d) => d.estimate_version_id === versionId) ?? null
  const manquants = manqueAuClient(fiche)
  const empeche = !frozen
    ? t('quote.needsFrozen')
    : !projet?.client_id
      ? t('quote.needsClient')
      : manquants.length > 0
        ? t('clients.requiredForIssuing')
        : celuiCi
          ? t('quote.alreadyIssued')
          : null

  async function emettre() {
    setErreur(null)
    setOccupe(true)
    try {
      await api.issueQuote(estimateId, versionId, {
        valid_until: validite || null,
        terms: conditions.trim() || null,
        include_internal_costs: couts,
      })
      setOuvert(false)
      await charger()
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  /**
   * Le PDF est authentifié : un `<a href>` partirait sans jeton.
   *
   * Les octets sont ceux du volume, tels qu'écrits à l'émission — pas un
   * document recomposé côté navigateur.
   */
  async function telecharger(quote: IssuedQuote) {
    setErreur(null)
    try {
      const blob = await api.fetchExport(api.issuedQuoteUrl(quote.id))
      const url = URL.createObjectURL(blob)
      const lien = document.createElement('a')
      lien.href = url
      lien.download = `devis-${quote.number.replace(/[^\w.-]+/g, '-')}.pdf`
      lien.click()
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (attrape) {
      setErreur(attrape)
    }
  }

  return (
    <section className="card" data-testid="emission-du-devis">
      <h2 style={{ marginTop: 0 }}>{t('quote.issueTitle')}</h2>
      <ErrorNotice error={erreur} />

      {celuiCi ? (
        <div className="notice success" role="status" data-testid="devis-emis">
          <strong>{t('quote.issued')}</strong> — {t('quote.number')}{' '}
          <span className="mono" data-testid="numero-du-devis">
            {celuiCi.number}
          </span>
          <div className="muted">
            {t('quote.issuedAt')} {dateFr(celuiCi.issued_at)} · {t('quote.validUntil')}{' '}
            {dateFr(celuiCi.valid_until)} · {t('quote.recipient')} : {celuiCi.client_name}
          </div>
        </div>
      ) : (
        <p className="muted">{empeche ?? t('quote.issueWarning')}</p>
      )}

      <div className="toolbar">
        {/* Masqué pour un rôle qui n'écrit pas d'estimation : l'API refuse. */}
        {peutEmettre && !celuiCi && (
          <button
            className="primary"
            disabled={empeche !== null}
            title={empeche ?? undefined}
            onClick={() => setOuvert((ouvert) => !ouvert)}
          >
            {t('quote.issue')}
          </button>
        )}
        {celuiCi && peutTelecharger && (
          <button
            className="primary"
            data-testid="telecharger-le-devis"
            onClick={() => void telecharger(celuiCi)}
          >
            {t('quote.download')}
          </button>
        )}
      </div>

      {ouvert && peutEmettre && !celuiCi && (
        <div className="card">
          <div className="notice warning" role="alert">
            {t('quote.issueWarning')}
          </div>
          {fiche && (
            <p data-testid="recapitulatif-client">
              <strong>{t('quote.recipient')} :</strong> {fiche.name}
              <br />
              <span className="muted">
                {[fiche.billing_address, [fiche.postal_code, fiche.city].filter(Boolean).join(' ')]
                  .filter(Boolean)
                  .join(', ')}
              </span>
            </p>
          )}
          <div className="row">
            <div className="field">
              <label htmlFor="valid-until">{t('quote.validUntil')}</label>
              <input
                id="valid-until"
                type="date"
                value={validite}
                onChange={(evenement) => setValidite(evenement.target.value)}
              />
              <span className="muted">{t('quote.validUntilHint')}</span>
            </div>
          </div>
          <div className="field">
            <label htmlFor="terms">{t('quote.terms')}</label>
            <textarea
              id="terms"
              rows={4}
              value={conditions}
              onChange={(evenement) => setConditions(evenement.target.value)}
            />
            <span className="muted">{t('quote.termsHint')}</span>
          </div>
          {/* Décocher n'est pas un défaut à contourner : les coûts internes
              n'apparaissent que sur décision explicite, et seulement pour qui
              a le droit de les exporter. */}
          {peutCoutsInternes && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <input
                type="checkbox"
                checked={couts}
                onChange={(evenement) => setCouts(evenement.target.checked)}
              />
              {t('quote.includeInternal')}
            </label>
          )}
          {couts && (
            <div className="notice warning" role="alert">
              {t('quote.includeInternalWarning')}
            </div>
          )}
          <p>
            <button
              className="primary"
              data-testid="confirmer-l-emission"
              onClick={() => void emettre()}
              disabled={occupe}
            >
              {t('common.confirm')}
            </button>{' '}
            <button onClick={() => setOuvert(false)}>{t('common.cancel')}</button>
          </p>
        </div>
      )}

      <h3>{t('quote.history')}</h3>
      {devis.length === 0 ? (
        <p className="muted">{t('quote.none')}</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>{t('quote.number')}</th>
              <th>{t('estimate.version')}</th>
              <th>{t('quote.issuedAt')}</th>
              <th>{t('quote.validUntil')}</th>
              <th>{t('quote.recipient')}</th>
              <th>{t('common.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {devis.map((quote) => (
              <tr key={quote.id} data-quote-number={quote.number}>
                <td className="mono">{quote.number}</td>
                <td>v{quote.version_number}</td>
                <td>{dateFr(quote.issued_at)}</td>
                <td>{dateFr(quote.valid_until)}</td>
                <td>{quote.client_name}</td>
                <td>
                  {peutTelecharger && (
                    <button onClick={() => void telecharger(quote)}>{t('quote.download')}</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
