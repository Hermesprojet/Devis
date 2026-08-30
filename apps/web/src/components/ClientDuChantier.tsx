'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api, manqueAuClient, type Client, type Project } from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * Le client d'un chantier : le choisir, ou en créer un.
 *
 * Un chantier antérieur au répertoire n'a qu'un `client_name` libre. On le
 * MONTRE — il reste l'information que l'utilisateur a saisie — et on demande
 * une sélection explicite, sans jamais convertir d'office : deux entreprises
 * homonymes seraient confondues, et le devis partirait à la mauvaise.
 */
export function ClientDuChantier({
  project,
  onChange,
}: {
  project: Project
  onChange: (projet: Project) => void
}) {
  const [fiches, setFiches] = useState<Client[]>([])
  const [choix, setChoix] = useState(project.client_id ?? '')
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)
  const permissions = usePermissions()
  const ecrire = can(permissions, PERMISSIONS.projectWrite)

  const charger = useCallback(async () => {
    try {
      setFiches(await api.clients())
    } catch (attrape) {
      setErreur(attrape)
    }
  }, [])

  useEffect(() => {
    void charger()
  }, [charger])

  useEffect(() => {
    setChoix(project.client_id ?? '')
  }, [project.client_id])

  const fiche = fiches.find((f) => f.id === project.client_id) ?? null
  const manquants = project.client_id ? manqueAuClient(fiche) : []

  async function rattacher() {
    setErreur(null)
    setOccupe(true)
    try {
      onChange(await api.updateProject(project.id, { client_id: choix || null }))
      await charger()
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>{t('clients.selectForProject')}</h2>
      <ErrorNotice error={erreur} />

      {fiche ? (
        <p data-testid="client-du-chantier">
          <strong>{fiche.name}</strong>
          {fiche.company_number && <span className="mono"> · {fiche.company_number}</span>}
          <br />
          <span className="muted">
            {[fiche.billing_address, [fiche.postal_code, fiche.city].filter(Boolean).join(' ')]
              .filter(Boolean)
              .join(', ') || t('common.none')}
          </span>
        </p>
      ) : (
        <div className="notice warning" role="status">
          {t('clients.attachHint')}
          {project.client_name && (
            <div className="muted" style={{ marginTop: 6 }}>
              {t('clients.legacyName')} : <strong>{project.client_name}</strong>
            </div>
          )}
        </div>
      )}

      {/* Ces fiches manquent de ce qu'il faut pour ADRESSER un devis. Le dire
          ici évite de le découvrir sur un refus, au moment d'émettre. */}
      {manquants.length > 0 && (
        <div className="notice warning" role="status">
          {t('clients.requiredForIssuing')}
        </div>
      )}

      {ecrire && (
        <div className="toolbar">
          <select
            aria-label={t('clients.selectForProject')}
            data-testid="selecteur-client"
            value={choix}
            onChange={(evenement) => setChoix(evenement.target.value)}
          >
            <option value="">{t('clients.noneSelected')}</option>
            {fiches.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name}
                {candidate.city ? ` — ${candidate.city}` : ''}
              </option>
            ))}
          </select>
          <button
            className="primary"
            onClick={() => void rattacher()}
            disabled={occupe || choix === (project.client_id ?? '')}
          >
            {t('clients.attach')}
          </button>
          <div className="spacer" />
          <Link href="/clients">{t('clients.new')}</Link>
        </div>
      )}
    </div>
  )
}
