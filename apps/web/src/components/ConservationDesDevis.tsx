'use client'

import { useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api, type OrgSettings } from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * La durée de conservation des devis émis.
 *
 * Le champ vide n'est PAS « sans limite » : c'est « la question n'a pas été
 * tranchée ». Dans cet état, le serveur refuse de détruire l'organisation. Le
 * refus conserve, et conserver est la position sûre quand la règle est
 * inconnue.
 *
 * L'écran ne suggère donc aucun nombre — ni sept ans, ni dix. Une durée de
 * conservation est une règle réglementaire : elle a une source officielle
 * datée et demande la validation d'un spécialiste. Un chiffre glissé dans le
 * `placeholder` se lirait comme une recommandation, et ce dépôt n'en a aucune
 * à donner.
 */
export function ConservationDesDevis({
  settings,
  onSaved,
}: {
  settings: OrgSettings
  onSaved: (settings: OrgSettings) => void
}) {
  const enTexte = (annees: number | null) => (annees === null ? '' : String(annees))
  const [saisie, setSaisie] = useState(enTexte(settings.quote_retention_years))
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)
  const [enregistre, setEnregistre] = useState(false)
  const permissions = usePermissions()
  const gerer = can(permissions, PERMISSIONS.orgManage)

  const nettoyee = saisie.trim()
  // La chaîne vide devient `null` — « non tranchée » — et part dans le PATCH.
  // C'est ce qui la distingue d'un champ omis, qui lui ne changerait rien.
  const valeur = nettoyee === '' ? null : Number(nettoyee)
  const invalide =
    valeur !== null && (!Number.isInteger(valeur) || valeur < 0 || valeur > 100)
  const inchange = valeur === settings.quote_retention_years

  async function enregistrer() {
    setOccupe(true)
    setErreur(null)
    setEnregistre(false)
    try {
      const misAJour = await api.updateOrganizationSettings({ quote_retention_years: valeur })
      onSaved(misAJour)
      setSaisie(enTexte(misAJour.quote_retention_years))
      setEnregistre(true)
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  const decidee = settings.quote_retention_years !== null
  const etat = decidee
    ? t('settings.retentionSet').replace('{n}', String(settings.quote_retention_years))
    : t('settings.retentionUndecided')

  return (
    <div className="card" data-testid="conservation-des-devis">
      <h2 style={{ marginTop: 0 }}>{t('settings.retention')}</h2>
      <p className="muted">{t('settings.retentionHint')}</p>
      <ErrorNotice error={erreur} />

      <p>
        <span
          className={decidee ? 'badge' : 'badge warning'}
          data-testid="etat-de-conservation"
          data-decidee={String(decidee)}
        >
          {etat}
        </span>
      </p>

      {gerer && (
        <>
          <div className="row">
            <div className="field">
              <label htmlFor="quote-retention-years">{t('settings.retentionYears')}</label>
              <input
                id="quote-retention-years"
                className="mono"
                inputMode="numeric"
                placeholder={t('settings.retentionPlaceholder')}
                value={saisie}
                onChange={(evenement) => setSaisie(evenement.target.value)}
              />
            </div>
          </div>
          {invalide && (
            <div className="notice warning" role="status">
              {t('settings.retentionInvalid')}
            </div>
          )}
          <p className="muted">{t('settings.retentionSource')}</p>
          <p>
            <button
              className="primary"
              onClick={() => void enregistrer()}
              disabled={occupe || inchange || invalide}
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
