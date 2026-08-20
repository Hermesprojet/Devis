'use client'

import { useEffect, useState } from 'react'

import { ErrorNotice, Loading } from '@/components/Feedback'
import { Shell } from '@/components/Shell'
import { api, type Organization, type OrgSettings } from '@/lib/api'
import { t } from '@/lib/i18n'

function percent(rate: string | null): string {
  if (rate === null) return t('common.none')
  return `${(Number(rate) * 100).toFixed(2)} %`
}

export default function SettingsPage() {
  const [organization, setOrganization] = useState<Organization | null>(null)
  const [settings, setSettings] = useState<OrgSettings | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    Promise.all([api.organization(), api.organizationSettings()])
      .then(([loadedOrganization, loadedSettings]) => {
        setOrganization(loadedOrganization)
        setSettings(loadedSettings)
      })
      .catch(setError)
  }, [])

  if (error) {
    return (
      <Shell>
        <ErrorNotice error={error} />
      </Shell>
    )
  }
  if (!organization || !settings) {
    return (
      <Shell>
        <Loading />
      </Shell>
    )
  }

  return (
    <Shell>
      <h1>{t('settings.title')}</h1>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>{organization.name}</h2>
        <table>
          <tbody>
            <tr>
              <td>Dénomination légale</td>
              <td>{organization.legal_name ?? t('common.none')}</td>
            </tr>
            <tr>
              <td>Numéro d&apos;entreprise</td>
              <td className="mono">{organization.company_number ?? t('common.none')}</td>
            </tr>
            <tr>
              <td>{t('projects.region')}</td>
              <td>
                <span className="badge">
                  {organization.country_code} / {organization.region_code}
                </span>
              </td>
            </tr>
            <tr>
              <td>Devise</td>
              <td>{organization.currency}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>{t('settings.rounding')}</h2>
        <table>
          <tbody>
            <tr>
              <td>Décimales des totaux</td>
              <td className="num">{settings.rounding_scale}</td>
            </tr>
            <tr>
              <td>Décimales des prix unitaires</td>
              <td className="num">{settings.unit_price_scale}</td>
            </tr>
            <tr>
              <td>Mode</td>
              <td>{settings.rounding_mode}</td>
            </tr>
            <tr>
              <td>{t('settings.missingPricePolicy')}</td>
              <td>
                <span className="badge">
                  {settings.missing_price_policy === 'block'
                    ? 'bloque le gel'
                    : 'avertit seulement'}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>{t('settings.markup')}</h2>
        {settings.commercial_rates_visible ? (
          <table>
            <tbody>
              <tr>
                <td>{t('settings.siteOverheads')}</td>
                <td className="num">{percent(settings.site_overheads_rate)}</td>
              </tr>
              <tr>
                <td>{t('settings.generalOverheads')}</td>
                <td className="num">{percent(settings.general_overheads_rate)}</td>
              </tr>
              <tr>
                <td>{t('settings.contingency')}</td>
                <td className="num">{percent(settings.contingency_rate)}</td>
              </tr>
              <tr>
                <td>{t('settings.margin')}</td>
                <td className="num">{percent(settings.margin_rate)}</td>
              </tr>
              <tr>
                <td>{t('settings.marginMethod')}</td>
                <td>
                  {settings.margin_method === 'on_price'
                    ? 'marge sur prix de vente'
                    : 'marge sur coût'}
                </td>
              </tr>
            </tbody>
          </table>
        ) : (
          <div className="notice info">{t('settings.masked')}</div>
        )}
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Modules à venir</h2>
        <p className="muted">
          Analyse documentaire, plans et CAO, fournisseurs et demandes de prix ne
          sont pas implémentés à ce stade. Ils sont décrits dans{' '}
          <span className="mono">docs/ROADMAP.md</span>. Aucun écran ne les
          présente comme disponibles.
        </p>
      </div>
    </Shell>
  )
}
