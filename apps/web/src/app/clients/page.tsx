'use client'

import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice, Loading } from '@/components/Feedback'
import { Shell } from '@/components/Shell'
import { api, manqueAuClient, type Client } from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/** Un formulaire vide. Le pays est prérempli : ce dépôt travaille en Belgique. */
const VIDE = {
  name: '',
  company_number: '',
  billing_address: '',
  postal_code: '',
  city: '',
  country_code: 'BE',
  contact_name: '',
  email: '',
  phone: '',
}

type Formulaire = typeof VIDE

function versFormulaire(fiche: Client): Formulaire {
  return {
    name: fiche.name,
    company_number: fiche.company_number ?? '',
    billing_address: fiche.billing_address ?? '',
    postal_code: fiche.postal_code ?? '',
    city: fiche.city ?? '',
    country_code: fiche.country_code,
    contact_name: fiche.contact_name ?? '',
    email: fiche.email ?? '',
    phone: fiche.phone ?? '',
  }
}

/** Une chaîne vide n'est pas une valeur : l'API veut `null`. */
export function corpsDeFiche(formulaire: Formulaire): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(formulaire).map(([cle, valeur]) => [cle, valeur.trim() || null]),
  )
}

/**
 * Le répertoire des clients.
 *
 * Deux fiches de même nom restent deux fiches : l'écran le dit plutôt que de
 * rapprocher tout seul. Ce qui manque pour ÉMETTRE est signalé ici, avant
 * d'arriver sur un refus au moment du devis.
 */
export default function ClientsPage() {
  const [fiches, setFiches] = useState<Client[] | null>(null)
  const [erreur, setErreur] = useState<unknown>(null)
  const [recherche, setRecherche] = useState('')
  const [archives, setArchives] = useState(false)
  const [formulaire, setFormulaire] = useState<Formulaire>(VIDE)
  const [edite, setEdite] = useState<string | null>(null)
  const [ouvert, setOuvert] = useState(false)
  const [occupe, setOccupe] = useState(false)
  const permissions = usePermissions()
  const ecrire = can(permissions, PERMISSIONS.projectWrite)

  const recharger = useCallback(async (q: string, avecArchives: boolean) => {
    const parametres = new URLSearchParams()
    if (q.trim()) parametres.set('q', q.trim())
    if (avecArchives) parametres.set('include_archived', 'true')
    const suffixe = parametres.toString()
    try {
      setFiches(await api.clients(suffixe ? `?${suffixe}` : ''))
    } catch (attrape) {
      setErreur(attrape)
    }
  }, [])

  useEffect(() => {
    void recharger(recherche, archives)
  }, [recharger, recherche, archives])

  async function enregistrer(evenement: React.FormEvent) {
    evenement.preventDefault()
    setErreur(null)
    setOccupe(true)
    try {
      const corps = corpsDeFiche(formulaire)
      if (edite) await api.updateClient(edite, corps)
      else await api.createClient(corps)
      setFormulaire(VIDE)
      setEdite(null)
      setOuvert(false)
      await recharger(recherche, archives)
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  async function archiver(fiche: Client) {
    setErreur(null)
    try {
      await api.archiveClient(fiche.id)
      await recharger(recherche, archives)
    } catch (attrape) {
      setErreur(attrape)
    }
  }

  async function reactiver(fiche: Client) {
    setErreur(null)
    try {
      await api.updateClient(fiche.id, { status: 'active' })
      await recharger(recherche, archives)
    } catch (attrape) {
      setErreur(attrape)
    }
  }

  const homonymes = new Set(
    (fiches ?? [])
      .map((f) => f.name.trim().toLowerCase())
      .filter((nom, index, tous) => tous.indexOf(nom) !== index),
  )

  return (
    <Shell>
      <h1>{t('clients.title')}</h1>
      <p className="muted">{t('clients.intro')}</p>
      <ErrorNotice error={erreur} />

      <div className="toolbar">
        <input
          style={{ maxWidth: 320 }}
          aria-label={t('clients.search')}
          placeholder={t('clients.search')}
          value={recherche}
          onChange={(evenement) => setRecherche(evenement.target.value)}
        />
        <label className="muted" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            checked={archives}
            onChange={(evenement) => setArchives(evenement.target.checked)}
          />
          {t('clients.showArchived')}
        </label>
        <div className="spacer" />
        {/* Masqué et non désactivé : l'API refuserait, et un bouton qui échoue
            n'apprend rien à qui ne peut pas écrire. */}
        {ecrire && (
          <button
            className="primary"
            onClick={() => {
              setEdite(null)
              setFormulaire(VIDE)
              setOuvert((ouvert) => !ouvert)
            }}
          >
            {t('clients.new')}
          </button>
        )}
      </div>

      {ouvert && ecrire && (
        <form className="card" onSubmit={enregistrer}>
          <h3 style={{ marginTop: 0 }}>{edite ? t('clients.edit') : t('clients.new')}</h3>
          <div className="row">
            <Champ
              id="client-name"
              label={t('clients.name')}
              requis
              valeur={formulaire.name}
              onChange={(v) => setFormulaire({ ...formulaire, name: v })}
            />
            <Champ
              id="client-company-number"
              label={t('clients.companyNumber')}
              valeur={formulaire.company_number}
              onChange={(v) => setFormulaire({ ...formulaire, company_number: v })}
            />
          </div>
          <div className="row">
            <Champ
              id="client-address"
              label={t('clients.billingAddress')}
              valeur={formulaire.billing_address}
              onChange={(v) => setFormulaire({ ...formulaire, billing_address: v })}
            />
            <Champ
              id="client-postal-code"
              label={t('clients.postalCode')}
              valeur={formulaire.postal_code}
              onChange={(v) => setFormulaire({ ...formulaire, postal_code: v })}
            />
            <Champ
              id="client-city"
              label={t('clients.city')}
              valeur={formulaire.city}
              onChange={(v) => setFormulaire({ ...formulaire, city: v })}
            />
          </div>
          <div className="row">
            <Champ
              id="client-contact"
              label={t('clients.contact')}
              valeur={formulaire.contact_name}
              onChange={(v) => setFormulaire({ ...formulaire, contact_name: v })}
            />
            <Champ
              id="client-email"
              label={t('clients.email')}
              valeur={formulaire.email}
              onChange={(v) => setFormulaire({ ...formulaire, email: v })}
            />
            <Champ
              id="client-phone"
              label={t('clients.phone')}
              valeur={formulaire.phone}
              onChange={(v) => setFormulaire({ ...formulaire, phone: v })}
            />
          </div>
          <p className="muted">{t('clients.requiredForIssuing')}</p>
          <button className="primary" type="submit" disabled={occupe}>
            {t('common.save')}
          </button>{' '}
          <button
            type="button"
            onClick={() => {
              setOuvert(false)
              setEdite(null)
            }}
          >
            {t('common.cancel')}
          </button>
        </form>
      )}

      {fiches === null ? (
        <Loading />
      ) : fiches.length === 0 ? (
        <div className="card muted">{t('clients.empty')}</div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>{t('clients.name')}</th>
                <th>{t('clients.companyNumber')}</th>
                <th>{t('clients.billingAddress')}</th>
                <th>{t('common.status')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {fiches.map((fiche) => {
                const manquants = manqueAuClient(fiche)
                return (
                  <tr key={fiche.id} data-client-id={fiche.id}>
                    <td>
                      {fiche.name}
                      {/* Deux fiches de même nom ne sont jamais fusionnées ;
                          l'écran le signale pour que l'utilisateur choisisse
                          la bonne, au lieu de le découvrir sur le devis. */}
                      {homonymes.has(fiche.name.trim().toLowerCase()) && (
                        <div className="muted" style={{ fontSize: 11 }}>
                          {t('clients.homonym')}
                        </div>
                      )}
                    </td>
                    <td className="mono">{fiche.company_number ?? t('common.none')}</td>
                    <td>
                      {[fiche.billing_address, [fiche.postal_code, fiche.city]
                        .filter(Boolean)
                        .join(' ')]
                        .filter(Boolean)
                        .join(', ') || t('common.none')}
                      {manquants.length > 0 && (
                        <div className="badge warning" style={{ marginTop: 4 }}>
                          {t('clients.incompleteForIssuing')}
                        </div>
                      )}
                    </td>
                    <td>
                      <span className={`badge ${fiche.status === 'active' ? 'success' : ''}`}>
                        {fiche.status === 'active' ? t('clients.active') : t('clients.archived')}
                      </span>
                    </td>
                    <td>
                      {ecrire && (
                        <>
                          <button
                            onClick={() => {
                              setEdite(fiche.id)
                              setFormulaire(versFormulaire(fiche))
                              setOuvert(true)
                            }}
                          >
                            {t('common.edit')}
                          </button>{' '}
                          {fiche.status === 'active' ? (
                            <button onClick={() => void archiver(fiche)}>
                              {t('clients.archive')}
                            </button>
                          ) : (
                            <button onClick={() => void reactiver(fiche)}>
                              {t('clients.restore')}
                            </button>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Shell>
  )
}

function Champ({
  id,
  label,
  valeur,
  onChange,
  requis,
}: {
  id: string
  label: string
  valeur: string
  onChange: (valeur: string) => void
  requis?: boolean
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        required={requis}
        value={valeur}
        onChange={(evenement) => onChange(evenement.target.value)}
      />
    </div>
  )
}
