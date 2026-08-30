'use client'

import { useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api } from '@/lib/api'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * L'écran d'une bibliothèque qui n'existe pas encore.
 *
 * Sans lui, une organisation neuve arrivait sur une page d'import dont la
 * cible — la version de bibliothèque — n'existait pas : le sélecteur était
 * vide et le bouton d'import n'avait nulle part où écrire. Une impasse qui ne
 * disait pas son nom.
 */
export function BibliothequeVide({ onCree }: { onCree: () => void }) {
  const [nom, setNom] = useState('Bibliothèque interne')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const permissions = usePermissions()

  async function creer(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      // L'API crée elle-même la « Version initiale » de la bibliothèque : en
      // ajouter une seconde ici donnerait deux versions vides à une
      // organisation qui n'en a demandé aucune.
      await api.createPriceBook({ name: nom.trim(), currency: 'EUR' })
      onCree()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  // Sans le droit d'écrire, l'écran explique l'absence plutôt que de
  // proposer une création qui serait refusée.
  if (!can(permissions, PERMISSIONS.pricebookWrite)) {
    return (
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Aucune bibliothèque de prix</h2>
        <p className="muted">
          Aucun prix n&apos;a encore été enregistré. Demandez à un responsable d&apos;étude de prix
          d&apos;en créer une.
        </p>
      </div>
    )
  }

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Aucune bibliothèque de prix</h2>
      <p className="muted">
        Une bibliothèque contient vos prix unitaires. Vous pourrez ensuite y saisir des prix à la
        main ou en importer un fichier CSV.
      </p>
      <ErrorNotice error={error} />
      <form onSubmit={creer}>
        <div className="field">
          <label htmlFor="biblio-nom">Nom de la bibliothèque</label>
          <input
            id="biblio-nom"
            required
            maxLength={160}
            value={nom}
            onChange={(event) => setNom(event.target.value)}
          />
        </div>
        <button className="primary" type="submit" disabled={busy}>
          Créer la bibliothèque
        </button>
      </form>
    </div>
  )
}
