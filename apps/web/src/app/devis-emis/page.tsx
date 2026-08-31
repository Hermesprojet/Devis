'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice, Loading } from '@/components/Feedback'
import { Shell } from '@/components/Shell'
import { api, type QuoteBoardRow } from '@/lib/api'
import { t } from '@/lib/i18n'

const ETATS = [
  ['', 'Tous les états'],
  ['issued', 'Émis'],
  ['transmitted', 'Transmis'],
  ['viewed', 'Consulté'],
  ['accepted', 'Accepté'],
  ['declined', 'Refusé'],
  ['expired', 'Expiré'],
] as const

function dateFr(valeur: string): string {
  return new Date(valeur).toLocaleDateString('fr-BE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
}

/** L'état, avec la couleur que l'écran lui donne. */
function Etat({ code, label }: { code: string; label: string }) {
  const classe =
    code === 'accepted' ? 'success' : code === 'declined' || code === 'expired' ? 'danger' : ''
  return <span className={`badge ${classe}`}>{label}</span>
}

/**
 * Le suivi commercial, tous chantiers confondus.
 *
 * C'est l'écran qui manquait : jusqu'ici un devis émis ne se retrouvait que
 * depuis SON chantier, ce qui suppose de savoir lequel. Une entreprise suit
 * ses devis par échéance et par état, pas par projet.
 */
export default function QuotesBoardPage() {
  const [lignes, setLignes] = useState<QuoteBoardRow[] | null>(null)
  const [erreur, setErreur] = useState<unknown>(null)
  const [recherche, setRecherche] = useState('')
  const [etat, setEtat] = useState('')
  const [depuis, setDepuis] = useState('')
  const [jusqua, setJusqua] = useState('')
  const [bientot, setBientot] = useState(false)

  const recharger = useCallback(async () => {
    const parametres = new URLSearchParams()
    if (recherche.trim()) parametres.set('q', recherche.trim())
    if (etat) parametres.set('state', etat)
    if (depuis) parametres.set('issued_from', depuis)
    if (jusqua) parametres.set('issued_to', jusqua)
    if (bientot) parametres.set('expiring_within_days', '14')
    const suffixe = parametres.toString()
    try {
      setLignes((await api.quotes(suffixe ? `?${suffixe}` : '')).items)
      setErreur(null)
    } catch (attrape) {
      setErreur(attrape)
    }
  }, [recherche, etat, depuis, jusqua, bientot])

  useEffect(() => {
    const minuteur = setTimeout(() => void recharger(), 200)
    return () => clearTimeout(minuteur)
  }, [recharger])

  return (
    <Shell>
      <h1>{t('quotes.title')}</h1>
      <p className="muted">{t('quotes.intro')}</p>
      <ErrorNotice error={erreur} />

      <div className="toolbar">
        <input
          style={{ maxWidth: 280 }}
          aria-label={t('quotes.search')}
          placeholder={t('quotes.search')}
          value={recherche}
          onChange={(evenement) => setRecherche(evenement.target.value)}
        />
        <select
          aria-label={t('common.status')}
          value={etat}
          onChange={(evenement) => setEtat(evenement.target.value)}
        >
          {ETATS.map(([valeur, libelle]) => (
            <option key={valeur} value={valeur}>
              {libelle}
            </option>
          ))}
        </select>
        <div className="field" style={{ maxWidth: 170 }}>
          <label htmlFor="depuis">{t('quotes.issuedFrom')}</label>
          <input
            id="depuis"
            type="date"
            value={depuis}
            onChange={(evenement) => setDepuis(evenement.target.value)}
          />
        </div>
        <div className="field" style={{ maxWidth: 170 }}>
          <label htmlFor="jusqua">{t('quotes.issuedTo')}</label>
          <input
            id="jusqua"
            type="date"
            value={jusqua}
            onChange={(evenement) => setJusqua(evenement.target.value)}
          />
        </div>
        <label className="muted" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            checked={bientot}
            onChange={(evenement) => setBientot(evenement.target.checked)}
          />
          {t('quotes.expiringSoon')}
        </label>
      </div>

      {lignes === null ? (
        <Loading />
      ) : lignes.length === 0 ? (
        <div className="card muted">{t('quotes.empty')}</div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>{t('quote.number')}</th>
                <th>{t('projects.client')}</th>
                <th>{t('nav.projects')}</th>
                <th className="num">{t('estimate.totalTTC')}</th>
                <th>{t('quote.issuedAt')}</th>
                <th>{t('quote.validUntil')}</th>
                <th>{t('common.status')}</th>
                <th>{t('quotes.lastActivity')}</th>
              </tr>
            </thead>
            <tbody>
              {lignes.map((ligne) => (
                <tr key={ligne.id} data-quote-number={ligne.number}>
                  <td className="mono">
                    <Link href={`/devis-emis/${ligne.id}`}>{ligne.number}</Link>
                  </td>
                  <td>{ligne.client_name}</td>
                  <td>
                    <span className="mono">{ligne.project_reference}</span> {ligne.project_name}
                  </td>
                  <td className="num">
                    {ligne.total_ttc} {ligne.currency}
                  </td>
                  <td>{dateFr(ligne.issued_at)}</td>
                  <td>{dateFr(ligne.valid_until)}</td>
                  <td>
                    <Etat code={ligne.state.code} label={ligne.state.label} />
                    {ligne.has_active_link && (
                      <>
                        {' '}
                        <span className="badge">{t('quotes.linkActive')}</span>
                      </>
                    )}
                  </td>
                  <td>
                    {ligne.state.last_activity_at
                      ? dateFr(ligne.state.last_activity_at)
                      : t('common.none')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Shell>
  )
}
