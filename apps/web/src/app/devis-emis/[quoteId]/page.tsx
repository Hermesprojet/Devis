'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice, Loading } from '@/components/Feedback'
import { Shell } from '@/components/Shell'
import { api, type IssuedQuoteDetail, type ShareLinkCreated } from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

const CANAUX = [
  ['email', 'Courriel'],
  ['public_link', 'Lien de consultation'],
  ['phone', 'Téléphone'],
  ['meeting', 'Rendez-vous'],
  ['other', 'Autre'],
] as const

function dateFr(valeur: string | null): string {
  if (!valeur) return t('common.none')
  return new Date(valeur).toLocaleString('fr-BE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * La fiche d'un devis remis : le document, son état, son histoire.
 *
 * Trois zones qui ne se mélangent pas — le document et son empreinte, qui ne
 * bougent jamais ; le lien de consultation, qui se crée et se révoque ; la
 * chronologie, qui s'allonge et ne se réécrit pas.
 */
export default function QuoteSheetPage() {
  const params = useParams<{ quoteId: string }>()
  const quoteId = params.quoteId

  const [fiche, setFiche] = useState<IssuedQuoteDetail | null>(null)
  const [erreur, setErreur] = useState<unknown>(null)
  const [pret, setPret] = useState(false)
  const [occupe, setOccupe] = useState(false)
  const [nouveauLien, setNouveauLien] = useState<ShareLinkCreated | null>(null)
  const [copie, setCopie] = useState(false)
  const [saisie, setSaisie] = useState<'transmitted' | 'accepted' | 'declined' | null>(null)
  const [canal, setCanal] = useState<string>('email')
  const [note, setNote] = useState('')
  const [repondant, setRepondant] = useState('')
  const [correction, setCorrection] = useState<string | null>(null)
  const [motif, setMotif] = useState('')
  const permissions = usePermissions()
  const peutPartager = can(permissions, PERMISSIONS.exportClient)
  const peutEcrire = can(permissions, PERMISSIONS.estimateWrite)

  const charger = useCallback(async () => {
    try {
      setFiche(await api.quote(quoteId))
      setErreur(null)
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setPret(true)
    }
  }, [quoteId])

  useEffect(() => {
    void charger()
  }, [charger])

  async function agir(action: () => Promise<unknown>) {
    setOccupe(true)
    setErreur(null)
    try {
      await action()
      await charger()
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  async function creerLeLien() {
    setErreur(null)
    setOccupe(true)
    try {
      // Le secret n'est rendu qu'ICI, et une seule fois. On le garde en
      // mémoire de la page le temps que l'utilisateur le copie ; il n'est
      // écrit dans aucun stockage du navigateur.
      setNouveauLien(await api.createShareLink(quoteId))
      setCopie(false)
      await charger()
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  async function copier(url: string) {
    try {
      await navigator.clipboard.writeText(url)
      setCopie(true)
    } catch {
      // Le presse-papiers peut être refusé (permission, contexte non sécurisé).
      // Le champ reste sélectionnable à la main : on ne perd rien.
      setCopie(false)
    }
  }

  async function telecharger() {
    setErreur(null)
    try {
      const blob = await api.fetchExport(api.issuedQuoteUrl(quoteId))
      const url = URL.createObjectURL(blob)
      const lien = document.createElement('a')
      lien.href = url
      lien.download = `devis-${fiche?.quote.number.replace(/[^\w.-]+/g, '-') ?? ''}.pdf`
      lien.click()
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (attrape) {
      setErreur(attrape)
    }
  }

  if (!pret) {
    return (
      <Shell>
        <Loading />
      </Shell>
    )
  }
  if (!fiche) {
    return (
      <Shell>
        <h1>{t('quote.issued')}</h1>
        <ErrorNotice error={erreur} />
      </Shell>
    )
  }

  const decide = fiche.state.decision !== null
  const lienActif = fiche.links.find((lien) => lien.active) ?? null

  return (
    <Shell>
      <h1>
        {t('quote.issued')} <span className="mono">{fiche.quote.number}</span>
      </h1>
      <p className="muted">
        <span
          className={`badge ${
            fiche.state.code === 'accepted'
              ? 'success'
              : fiche.state.code === 'declined' || fiche.state.code === 'expired'
                ? 'danger'
                : ''
          }`}
          data-testid="etat-du-devis"
        >
          {fiche.state.label}
        </span>{' '}
        · <Link href={`/projets/${fiche.quote.project_id}`}>{fiche.project_reference}</Link>{' '}
        {fiche.project_name}
      </p>
      <ErrorNotice error={erreur} />

      <div className="card">
        <div className="row">
          <div className="field">
            <strong>{t('quote.recipient')}</strong>
            <div data-testid="destinataire-fige">
              {fiche.client_snapshot.name}
              <div className="muted">{fiche.client_snapshot.billing_address}</div>
              <div className="muted">
                {fiche.client_snapshot.postal_code} {fiche.client_snapshot.city}
              </div>
            </div>
            <span className="muted" style={{ fontSize: 11 }}>
              {t('quote.snapshotHint')}
            </span>
          </div>
          <div className="field">
            <strong>{t('estimate.totalTTC')}</strong>
            <div>
              {fiche.total_ttc} {fiche.currency}
            </div>
            <strong style={{ marginTop: 8 }}>{t('quote.validUntil')}</strong>
            <div>{new Date(fiche.quote.valid_until).toLocaleDateString('fr-BE')}</div>
          </div>
        </div>
        <div className="toolbar">
          {peutPartager && (
            <button onClick={() => void telecharger()}>{t('quote.download')}</button>
          )}
        </div>
        <p className="mono muted" style={{ fontSize: 12 }} data-testid="empreinte-interne">
          {t('quote.digest')} : {fiche.quote.pdf_sha256}
        </p>
      </div>

      {/* ---- le lien de consultation ---- */}
      <div className="card" data-testid="lien-de-consultation">
        <h2 style={{ marginTop: 0 }}>{t('quote.shareTitle')}</h2>
        <p className="muted">{t('quote.shareHint')}</p>

        {nouveauLien && (
          <div className="notice success" role="status">
            <p>{t('quote.shareOnce')}</p>
            <input
              readOnly
              className="mono"
              data-testid="url-du-lien"
              value={nouveauLien.url}
              onFocus={(evenement) => evenement.currentTarget.select()}
              style={{ width: '100%' }}
            />
            <p>
              <button onClick={() => void copier(nouveauLien.url)}>
                {copie ? t('quote.copied') : t('quote.copy')}
              </button>
            </p>
          </div>
        )}

        {fiche.links.length === 0 ? (
          <p className="muted">{t('quote.noLink')}</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>{t('common.status')}</th>
                <th>{t('quote.createdAt')}</th>
                <th>{t('quote.expiresAt')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {fiche.links.map((lien) => (
                <tr key={lien.id}>
                  <td>
                    <span className={`badge ${lien.active ? 'success' : ''}`}>
                      {lien.active
                        ? t('quote.linkActive')
                        : lien.revoked_at
                          ? t('quote.linkRevoked')
                          : t('quote.linkExpired')}
                    </span>
                  </td>
                  <td>{dateFr(lien.created_at)}</td>
                  <td>{dateFr(lien.expires_at)}</td>
                  <td>
                    {lien.active && peutPartager && (
                      <button
                        data-testid="revoquer-le-lien"
                        onClick={() =>
                          void agir(() => api.revokeShareLink(quoteId, lien.id))
                        }
                        disabled={occupe}
                      >
                        {t('quote.revoke')}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {peutPartager && (
          <p>
            <button
              className="primary"
              data-testid="creer-le-lien"
              onClick={() => void creerLeLien()}
              disabled={occupe}
            >
              {lienActif ? t('quote.newLink') : t('quote.createLink')}
            </button>
          </p>
        )}
      </div>

      {/* ---- ce que l'entreprise enregistre elle-même ---- */}
      {peutEcrire && (
        <div className="card" data-testid="saisie-hors-ligne">
          <h2 style={{ marginTop: 0 }}>{t('quote.offlineTitle')}</h2>
          <p className="muted">{t('quote.offlineHint')}</p>
          <div className="toolbar">
            <button data-testid="marquer-transmis" onClick={() => setSaisie('transmitted')}>
              {t('quote.markTransmitted')}
            </button>
            {!decide && (
              <>
                <button data-testid="acceptation-hors-ligne" onClick={() => setSaisie('accepted')}>
                  {t('quote.recordAccepted')}
                </button>
                <button data-testid="refus-hors-ligne" onClick={() => setSaisie('declined')}>
                  {t('quote.recordDeclined')}
                </button>
              </>
            )}
          </div>

          {saisie && (
            <div className="card">
              <div className="row">
                <div className="field">
                  <label htmlFor="canal">{t('quote.channel')}</label>
                  <select
                    id="canal"
                    value={canal}
                    onChange={(evenement) => setCanal(evenement.target.value)}
                  >
                    {CANAUX.map(([valeur, libelle]) => (
                      <option key={valeur} value={valeur}>
                        {libelle}
                      </option>
                    ))}
                  </select>
                </div>
                {saisie !== 'transmitted' && (
                  <div className="field">
                    <label htmlFor="repondant">{t('quote.respondent')}</label>
                    <input
                      id="repondant"
                      value={repondant}
                      onChange={(evenement) => setRepondant(evenement.target.value)}
                    />
                  </div>
                )}
              </div>
              <div className="field">
                <label htmlFor="note">
                  {saisie === 'transmitted' ? t('quote.noteOptional') : t('quote.noteRequired')}
                </label>
                <textarea
                  id="note"
                  rows={2}
                  value={note}
                  onChange={(evenement) => setNote(evenement.target.value)}
                />
              </div>
              <p>
                <button
                  className="primary"
                  data-testid="confirmer-saisie"
                  disabled={occupe || (saisie !== 'transmitted' && !note.trim())}
                  onClick={() =>
                    void agir(async () => {
                      await api.recordQuoteEvent(quoteId, {
                        kind: saisie,
                        channel: canal,
                        respondent_name: repondant.trim() || null,
                        comment: note.trim() || null,
                      })
                      setSaisie(null)
                      setNote('')
                      setRepondant('')
                    })
                  }
                >
                  {t('common.save')}
                </button>{' '}
                <button onClick={() => setSaisie(null)}>{t('common.cancel')}</button>
              </p>
            </div>
          )}
        </div>
      )}

      {/* ---- la chronologie ---- */}
      <div className="card" data-testid="chronologie">
        <h2 style={{ marginTop: 0 }}>{t('quote.timeline')}</h2>
        {fiche.events.length === 0 ? (
          <p className="muted">{t('quote.timelineEmpty')}</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>{t('quote.event')}</th>
                <th>{t('quote.channel')}</th>
                <th>{t('quote.who')}</th>
                <th>{t('quote.effectiveAt')}</th>
                <th>{t('quote.recordedAt')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {fiche.events.map((evenement) => (
                <tr
                  key={evenement.id}
                  data-event-kind={evenement.kind}
                  style={
                    evenement.corrected
                      ? { textDecoration: 'line-through', opacity: 0.6 }
                      : undefined
                  }
                >
                  <td>
                    {evenement.kind_label}
                    {evenement.comment && <div className="muted">{evenement.comment}</div>}
                    {evenement.corrected && (
                      <div className="muted" data-testid="motif-de-correction">
                        {t('quote.correctedBecause')} {evenement.correction_reason}
                      </div>
                    )}
                  </td>
                  <td>{evenement.channel ?? t('common.none')}</td>
                  <td>
                    {evenement.respondent_name ?? evenement.actor_email ?? t('common.none')}
                    {evenement.respondent_email && (
                      <div className="muted">{evenement.respondent_email}</div>
                    )}
                  </td>
                  <td>{dateFr(evenement.effective_at)}</td>
                  <td>{dateFr(evenement.recorded_at)}</td>
                  <td>
                    {peutEcrire &&
                      !evenement.corrected &&
                      evenement.kind !== 'correction' && (
                        <button onClick={() => setCorrection(evenement.id)}>
                          {t('quote.correct')}
                        </button>
                      )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {correction && (
          <div className="card">
            <div className="notice warning">{t('quote.correctionHint')}</div>
            <div className="field">
              <label htmlFor="motif-correction">{t('quote.correctionReason')}</label>
              <input
                id="motif-correction"
                value={motif}
                onChange={(evenement) => setMotif(evenement.target.value)}
              />
            </div>
            <p>
              <button
                className="primary"
                disabled={occupe || !motif.trim()}
                onClick={() =>
                  void agir(async () => {
                    await api.correctQuoteEvent(quoteId, correction, { reason: motif.trim() })
                    setCorrection(null)
                    setMotif('')
                  })
                }
              >
                {t('common.confirm')}
              </button>{' '}
              <button onClick={() => setCorrection(null)}>{t('common.cancel')}</button>
            </p>
          </div>
        )}
      </div>
    </Shell>
  )
}
