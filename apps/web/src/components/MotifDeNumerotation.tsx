'use client'

import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api, type OrgSettings, type QuoteNumberPreview } from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * Le motif de numérotation des devis, avec son aperçu.
 *
 * L'aperçu vient du SERVEUR, à chaque frappe. Recopier la règle de rendu ici
 * donnerait deux vérités : l'écran finirait par annoncer un format que l'API
 * n'applique pas — précisément ce que le repli silencieux d'hier laissait
 * passer, quand un motif fautif produisait des numéros par défaut sans que
 * rien ne le dise.
 */
export function MotifDeNumerotation({
  settings,
  onSaved,
}: {
  settings: OrgSettings
  onSaved: (settings: OrgSettings) => void
}) {
  const [motif, setMotif] = useState(settings.quote_number_pattern)
  const [verdict, setVerdict] = useState<QuoteNumberPreview | null>(null)
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)
  const [enregistre, setEnregistre] = useState(false)
  const permissions = usePermissions()
  const gerer = can(permissions, PERMISSIONS.orgManage)

  const sonder = useCallback(async (candidat: string) => {
    try {
      setVerdict(await api.quoteNumberPreview(candidat))
    } catch {
      // Un aperçu indisponible ne doit pas masquer le champ : l'API refusera
      // de toute façon un motif inutilisable au moment d'enregistrer.
      setVerdict(null)
    }
  }, [])

  useEffect(() => {
    if (!gerer) return
    const minuteur = setTimeout(() => void sonder(motif), 250)
    return () => clearTimeout(minuteur)
  }, [motif, gerer, sonder])

  async function enregistrer() {
    setErreur(null)
    setEnregistre(false)
    setOccupe(true)
    try {
      const misAJour = await api.updateOrganizationSettings({ quote_number_pattern: motif })
      onSaved(misAJour)
      setMotif(misAJour.quote_number_pattern)
      setEnregistre(true)
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  return (
    <div className="card" data-testid="motif-de-numerotation">
      <h2 style={{ marginTop: 0 }}>{t('settings.quoteNumbering')}</h2>
      <p className="muted">{t('settings.quoteNumberingHint')}</p>
      <ErrorNotice error={erreur} />

      {!gerer ? (
        <table>
          <tbody>
            <tr>
              <td>{t('settings.quoteNumberPattern')}</td>
              <td className="mono">{settings.quote_number_pattern}</td>
            </tr>
            <tr>
              <td>{t('settings.quoteNumberPreview')}</td>
              <td className="mono">{settings.quote_number_preview}</td>
            </tr>
          </tbody>
        </table>
      ) : (
        <>
          <div className="row">
            <div className="field" style={{ flex: '2 1 300px' }}>
              <label htmlFor="quote-number-pattern">{t('settings.quoteNumberPattern')}</label>
              <input
                id="quote-number-pattern"
                className="mono"
                value={motif}
                onChange={(evenement) => setMotif(evenement.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="quote-number-preview">{t('settings.quoteNumberPreview')}</label>
              <output
                id="quote-number-preview"
                className="mono"
                data-testid="apercu-du-numero"
                data-valide={verdict === null ? 'inconnu' : String(verdict.valid)}
              >
                {verdict === null
                  ? settings.quote_number_preview
                  : (verdict.preview ?? t('settings.quoteNumberRefused'))}
              </output>
            </div>
          </div>
          {verdict !== null && !verdict.valid && (
            <div className="notice warning" role="status">
              {verdict.message}
            </div>
          )}
          <p>
            <button
              className="primary"
              onClick={() => void enregistrer()}
              disabled={occupe || motif === settings.quote_number_pattern}
            >
              {t('common.save')}
            </button>{' '}
            {enregistre && <span className="badge success">{t('settings.saved')}</span>}
          </p>
        </>
      )}
    </div>
  )
}
