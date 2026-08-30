'use client'

import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api, type Member } from '@/lib/api'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * L'équipe de l'organisation, et le seul endroit où on la compose.
 *
 * Avant, `bootstrap` créait UN administrateur et rien ne permettait d'ajouter
 * qui que ce soit : une organisation neuve restait à une personne, et faire
 * entrer un métreur demandait d'écrire en base. C'est exactement le genre
 * d'impasse que ce parcours doit supprimer.
 *
 * Aucun mot de passe n'est créé et aucun message n'est envoyé : ce que l'on
 * inscrit, c'est le droit d'entrer. La personne se connecte par le
 * fournisseur d'identité de l'entreprise, et la liaison se fait à sa première
 * connexion sur son adresse vérifiée.
 */

/** Les rôles proposés, dans l'ordre où ils se choisissent réellement. */
const ROLES: ReadonlyArray<{ valeur: string; libelle: string }> = [
  { valeur: 'estimator', libelle: 'Métreur / deviseur' },
  { valeur: 'estimating_manager', libelle: 'Responsable étude de prix' },
  { valeur: 'project_manager', libelle: 'Chef de projet / conducteur' },
  { valeur: 'buyer', libelle: 'Acheteur' },
  { valeur: 'viewer', libelle: 'Lecteur / auditeur' },
  { valeur: 'org_admin', libelle: "Administrateur de l'entreprise" },
]

export function Collaborateurs() {
  const permissions = usePermissions()
  const gerer = can(permissions, PERMISSIONS.userManage)

  const [membres, setMembres] = useState<Member[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [ouvert, setOuvert] = useState(false)
  const [form, setForm] = useState({ email: '', full_name: '', role: 'estimator' })

  const charger = useCallback(async () => {
    try {
      setMembres(await api.members())
    } catch (caught) {
      setError(caught)
      setMembres([])
    }
  }, [])

  useEffect(() => {
    if (gerer) void charger()
  }, [charger, gerer])

  async function ajouter(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.inviteMember({
        email: form.email.trim(),
        full_name: form.full_name.trim(),
        role: form.role,
      })
      setForm({ email: '', full_name: '', role: 'estimator' })
      setOuvert(false)
      await charger()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  async function basculer(membre: Member) {
    setBusy(true)
    setError(null)
    try {
      await api.updateMember(membre.id, { is_active: !membre.is_active })
      await charger()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  async function changerRole(membre: Member, role: string) {
    setBusy(true)
    setError(null)
    try {
      await api.updateMember(membre.id, { role })
      await charger()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  // Un rôle sans `user:manage` ne voit pas cet encart : la liste des
  // collaborateurs et de leurs droits n'a pas à être exposée à qui ne peut
  // rien en faire.
  if (!gerer) return null
  if (membres === null) return <div className="card muted">Chargement des collaborateurs…</div>

  return (
    <div className="card" data-testid="collaborateurs">
      <h2 style={{ marginTop: 0 }}>Collaborateurs</h2>
      <p className="muted" style={{ fontSize: 13 }}>
        Ajouter quelqu&apos;un lui ouvre le droit d&apos;entrer, rien de plus : aucun mot de passe
        n&apos;est créé et aucun message n&apos;est envoyé. La personne se connecte avec le compte
        de l&apos;entreprise, et son compte est lié à sa première connexion.
      </p>

      <ErrorNotice error={error} />

      <table>
        <thead>
          <tr>
            <th>Adresse</th>
            <th>Nom</th>
            <th>Rôle</th>
            <th>Accès</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {membres.map((membre) => (
            <tr key={membre.id}>
              <td className="mono">{membre.email}</td>
              <td>{membre.full_name}</td>
              <td>
                <select
                  aria-label={`Rôle de ${membre.email}`}
                  value={membre.role}
                  disabled={busy}
                  onChange={(e) => void changerRole(membre, e.target.value)}
                >
                  {ROLES.map((role) => (
                    <option key={role.valeur} value={role.valeur}>
                      {role.libelle}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <span className={`badge ${membre.is_active ? 'success' : ''}`}>
                  {membre.is_active ? 'actif' : 'retiré'}
                </span>
              </td>
              <td className="num">
                <button type="button" disabled={busy} onClick={() => void basculer(membre)}>
                  {membre.is_active ? "Retirer l'accès" : "Rendre l'accès"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {ouvert ? (
        <form onSubmit={ajouter} style={{ marginTop: 16 }}>
          <div className="field">
            <label htmlFor="membre-email">Adresse e-mail</label>
            <input
              id="membre-email"
              type="email"
              required
              maxLength={255}
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="membre-nom">Nom affiché</label>
            <input
              id="membre-nom"
              required
              maxLength={200}
              value={form.full_name}
              onChange={(e) => setForm({ ...form, full_name: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="membre-role">Rôle</label>
            <select
              id="membre-role"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
            >
              {ROLES.map((role) => (
                <option key={role.valeur} value={role.valeur}>
                  {role.libelle}
                </option>
              ))}
            </select>
          </div>
          <button className="primary" type="submit" disabled={busy}>
            Ajouter le collaborateur
          </button>{' '}
          <button type="button" onClick={() => setOuvert(false)}>
            Annuler
          </button>
        </form>
      ) : (
        <button className="primary" style={{ marginTop: 12 }} onClick={() => setOuvert(true)}>
          Ajouter un collaborateur
        </button>
      )}
    </div>
  )
}
