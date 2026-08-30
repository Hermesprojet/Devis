'use client'

import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api, type TaxRate } from '@/lib/api'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/** Un taux est-il en vigueur aujourd'hui ? */
export function enVigueur(taux: TaxRate, aujourdhui = new Date()): boolean {
  if (!taux.is_default) return false
  const jour = aujourdhui.toISOString().slice(0, 10)
  if (taux.applies_from && taux.applies_from > jour) return false
  if (taux.applies_to && taux.applies_to < jour) return false
  return true
}

function pourcentage(taux: string): string {
  return `${(Number(taux) * 100).toFixed(2)} %`
}

const AUJOURDHUI = () => new Date().toISOString().slice(0, 10)

export function TauxDeTaxe({ onChange }: { onChange?: (taux: TaxRate[]) => void }) {
  const permissions = usePermissions()
  const gerer = can(permissions, PERMISSIONS.orgManage)

  const [taux, setTaux] = useState<TaxRate[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [ouvert, setOuvert] = useState(false)
  const [form, setForm] = useState({ code: '', label: '', pourcent: '', source: '' })

  const charger = useCallback(async () => {
    try {
      const liste = await api.taxRates()
      setTaux(liste)
      onChange?.(liste)
    } catch (caught) {
      setError(caught)
    }
    // `onChange` change à chaque rendu du parent ; l'inclure relancerait le
    // chargement en boucle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    void charger()
  }, [charger])

  async function creer(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      // L'administrateur saisit un POURCENTAGE ; l'API attend une proportion.
      // Faire la conversion ici évite de lui demander d'écrire « 0,21 ».
      const proportion = (Number(form.pourcent.replace(',', '.')) / 100).toString()
      await api.createTaxRate({
        code: form.code.trim(),
        label: form.label.trim(),
        rate: proportion,
        source: form.source.trim() || null,
      })
      setForm({ code: '', label: '', pourcent: '', source: '' })
      setOuvert(false)
      await charger()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  async function retirer(cible: TaxRate) {
    // Borner dans le temps plutôt que supprimer : les devis déjà gelés
    // gardent la trace de ce qui leur a été appliqué.
    setBusy(true)
    setError(null)
    try {
      await api.updateTaxRate(cible.id, { applies_to: AUJOURDHUI(), is_default: false })
      await charger()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  async function supprimer(cible: TaxRate) {
    setBusy(true)
    setError(null)
    try {
      await api.deleteTaxRate(cible.id)
      await charger()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  if (taux === null) return <div className="card muted">Chargement des taux…</div>

  const actifs = taux.filter((t) => enVigueur(t))

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Taux de taxe</h2>

      <p className="muted" style={{ fontSize: 13 }}>
        Metreo n&apos;installe aucun taux et n&apos;en devine aucun : le taux applicable, sa date
        d&apos;effet et sa base légale sont votre décision. Metreo ne les valide pas juridiquement.
      </p>

      <ErrorNotice error={error} />

      {taux.length === 0 ? (
        <div className="notice warning" role="status">
          Aucun taux configuré. Tant qu&apos;il n&apos;y en a pas, un devis affichera un TTC égal
          au HT.
        </div>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Code</th>
                <th>Libellé</th>
                <th className="num">Taux</th>
                <th>Période</th>
                <th>État</th>
                {gerer && <th />}
              </tr>
            </thead>
            <tbody>
              {taux.map((ligne) => {
                const actif = enVigueur(ligne)
                return (
                  <tr key={ligne.id}>
                    <td className="mono">{ligne.code}</td>
                    <td>{ligne.label}</td>
                    <td className="num">{pourcentage(ligne.rate)}</td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {ligne.applies_from ?? '—'} → {ligne.applies_to ?? '—'}
                    </td>
                    <td>
                      <span className={`badge ${actif ? 'success' : ''}`}>
                        {actif ? 'en vigueur' : 'retiré'}
                      </span>
                    </td>
                    {gerer && (
                      <td className="num">
                        {actif ? (
                          <button type="button" disabled={busy} onClick={() => void retirer(ligne)}>
                            Retirer du service
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={busy}
                            title="Refusé si un devis gelé porte déjà ce taux"
                            onClick={() => void supprimer(ligne)}
                          >
                            Supprimer
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>

          {actifs.length > 1 && (
            <div className="notice warning" role="status" style={{ marginTop: 12 }}>
              <strong>{actifs.length} taux sont en vigueur en même temps.</strong> Ils
              s&apos;appliquent <em>tous</em> à chaque ligne, et se cumulent :{' '}
              {actifs.map((a) => a.code).join(' + ')} ={' '}
              {pourcentage(String(actifs.reduce((somme, a) => somme + Number(a.rate), 0)))} au
              total. Retirez-en si ce n&apos;est pas ce que vous voulez facturer.
            </div>
          )}
        </>
      )}

      {gerer &&
        (ouvert ? (
          <form onSubmit={creer} style={{ marginTop: 16 }}>
            <div className="field">
              <label htmlFor="taxe-code">Code</label>
              <input
                id="taxe-code"
                required
                maxLength={30}
                placeholder="TVA-21"
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="taxe-label">Libellé imprimé sur le devis</label>
              <input
                id="taxe-label"
                required
                maxLength={120}
                placeholder="TVA 21 %"
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="taxe-taux">Taux (en pourcentage)</label>
              <input
                id="taxe-taux"
                required
                inputMode="decimal"
                placeholder="21"
                value={form.pourcent}
                onChange={(e) => setForm({ ...form, pourcent: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="taxe-source">Source (facultatif)</label>
              <input
                id="taxe-source"
                maxLength={255}
                placeholder="D'où vient ce taux"
                value={form.source}
                onChange={(e) => setForm({ ...form, source: e.target.value })}
              />
            </div>
            <button className="primary" type="submit" disabled={busy}>
              Enregistrer le taux
            </button>{' '}
            <button type="button" onClick={() => setOuvert(false)}>
              Annuler
            </button>
          </form>
        ) : (
          <button className="primary" style={{ marginTop: 12 }} onClick={() => setOuvert(true)}>
            Ajouter un taux de taxe
          </button>
        ))}
    </div>
  )
}
