'use client'

import { useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api } from '@/lib/api'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

const UNITES = ['m3', 'm2', 'm', 'kg', 't', 'h', 'pce', 'fft']

/**
 * Saisie d'un prix unitaire à la main.
 *
 * L'import CSV existait déjà, et il reste la bonne voie pour un catalogue.
 * Mais exiger un fichier pour le premier prix d'une entreprise neuve force à
 * quitter l'application avant d'avoir vu un seul devis.
 */
export function NouveauPrix({ versionId, onCree }: { versionId: string; onCree: () => void }) {
  const [ouvert, setOuvert] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [form, setForm] = useState({ code: '', label: '', unit_code: 'm3', unit_price: '' })
  const permissions = usePermissions()

  async function creer(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.createPriceItem(versionId, {
        code: form.code.trim(),
        label: form.label.trim(),
        unit_code: form.unit_code,
        unit_price: form.unit_price.replace(',', '.'),
        resource_kind: 'material',
        currency: 'EUR',
      })
      setForm({ code: '', label: '', unit_code: form.unit_code, unit_price: '' })
      setOuvert(false)
      onCree()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  // Avant TOUT rendu : placée plus bas, elle passait après le retour anticipé
  // ci-dessous et ne s'appliquait jamais au bouton lui-même.
  if (!can(permissions, PERMISSIONS.pricebookWrite)) return null

  if (!ouvert) {
    return (
      <button className="primary" onClick={() => setOuvert(true)}>
        Ajouter un prix
      </button>
    )
  }


  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Nouveau prix</h3>
      <ErrorNotice error={error} />
      <form onSubmit={creer}>
        <div className="field">
          <label htmlFor="prix-code">Code</label>
          <input
            id="prix-code"
            required
            value={form.code}
            onChange={(e) => setForm({ ...form, code: e.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor="prix-label">Désignation</label>
          <input
            id="prix-label"
            required
            value={form.label}
            onChange={(e) => setForm({ ...form, label: e.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor="prix-unite">Unité</label>
          <select
            id="prix-unite"
            value={form.unit_code}
            onChange={(e) => setForm({ ...form, unit_code: e.target.value })}
          >
            {UNITES.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="prix-montant">Prix unitaire HT (EUR)</label>
          <input
            id="prix-montant"
            required
            inputMode="decimal"
            value={form.unit_price}
            onChange={(e) => setForm({ ...form, unit_price: e.target.value })}
          />
        </div>
        <button className="primary" type="submit" disabled={busy}>
          Enregistrer le prix
        </button>{' '}
        <button type="button" onClick={() => setOuvert(false)}>
          Annuler
        </button>
      </form>
    </div>
  )
}
