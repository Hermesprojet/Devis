'use client'

import { useEffect, useState } from 'react'

import { api, type CompositePrice, type PriceItem } from '@/lib/api'
import { t } from '@/lib/i18n'

/**
 * D'où vient le prix d'un poste : de nulle part, de la bibliothèque, ou d'un
 * sous-détail.
 *
 * L'écran ne proposait qu'un sélecteur « Prix unitaire », et affichait un
 * badge « sous-détail » en lecture quand un identifiant existait — posé par
 * appel d'API, jamais depuis l'interface. Les deux sources sont désormais
 * offertes côte à côte, et **exclusives** : choisir l'une remet l'autre à
 * `null` explicitement dans la requête. L'API refuse de toute façon leur
 * cumul ; l'envoyer explicitement évite que ce refus arrive à l'utilisateur
 * pour une exclusivité qu'il croyait avoir respectée.
 */

export type Source = 'none' | 'library' | 'composite'

export type Choix = {
  source: Source
  price_item_id: string | null
  composite_price_id: string | null
}

export function sourceDe(item: {
  price_item_id: string | null
  composite_price_id: string | null
}): Choix {
  if (item.composite_price_id) {
    return { source: 'composite', price_item_id: null, composite_price_id: item.composite_price_id }
  }
  if (item.price_item_id) {
    return { source: 'library', price_item_id: item.price_item_id, composite_price_id: null }
  }
  return { source: 'none', price_item_id: null, composite_price_id: null }
}

export function SourceDePrix({
  choix,
  prix,
  composites,
  onChange,
  identifiant,
}: {
  choix: Choix
  prix: PriceItem[]
  composites: CompositePrice[]
  onChange: (choix: Choix) => void
  identifiant: string
}) {
  return (
    <>
      <div className="field">
        <label htmlFor={`source-${identifiant}`}>{t('priceSource.label')}</label>
        <select
          id={`source-${identifiant}`}
          value={choix.source}
          onChange={(evenement) => {
            const source = evenement.target.value as Source
            // Basculer de source EFFACE l'autre côté, dans le même geste. Sans
            // cela un poste garderait un identifiant devenu invisible à
            // l'écran, et le devis se calculerait sur une source que personne
            // ne voit plus.
            onChange({ source, price_item_id: null, composite_price_id: null })
          }}
        >
          <option value="none">{t('priceSource.none')}</option>
          <option value="library">{t('priceSource.library')}</option>
          <option value="composite">{t('priceSource.composite')}</option>
        </select>
      </div>

      {choix.source === 'library' && (
        <div className="field" style={{ flex: '2 1 240px' }}>
          <label htmlFor={`prix-${identifiant}`}>{t('priceSource.library')}</label>
          <select
            id={`prix-${identifiant}`}
            value={choix.price_item_id ?? ''}
            onChange={(evenement) =>
              onChange({
                source: 'library',
                price_item_id: evenement.target.value || null,
                composite_price_id: null,
              })
            }
          >
            <option value="">{t('priceSource.pick')}</option>
            {prix.map((p) => (
              <option key={p.id} value={p.id}>
                {p.code} — {p.label} ({p.unit_price} €/{p.unit_code})
              </option>
            ))}
          </select>
        </div>
      )}

      {choix.source === 'composite' && (
        <div className="field" style={{ flex: '2 1 240px' }}>
          <label htmlFor={`composite-${identifiant}`}>{t('priceSource.composite')}</label>
          <select
            id={`composite-${identifiant}`}
            value={choix.composite_price_id ?? ''}
            onChange={(evenement) =>
              onChange({
                source: 'composite',
                price_item_id: null,
                composite_price_id: evenement.target.value || null,
              })
            }
          >
            <option value="">{t('priceSource.pick')}</option>
            {composites.map((c) => (
              <option key={c.id} value={c.id}>
                {c.code} — {c.label} ({c.components.length} composants, /{c.unit_code})
              </option>
            ))}
          </select>
        </div>
      )}
    </>
  )
}

/**
 * Ce qu'un poste affiche quand son prix vient d'un sous-détail.
 *
 * Le coût unitaire est **prévisualisé par le serveur**, jamais recalculé ici :
 * c'est le même moteur que l'estimation, et deux arithmétiques divergeraient
 * au premier arrondi. Quand il n'est pas calculable — une conversion sans
 * densité, une unité qui ne se convertit pas — l'écran le dit au lieu
 * d'afficher un blanc que l'on prendrait pour zéro.
 */
export function ResumeDuSousDetail({
  composite,
  versionId,
}: {
  composite: CompositePrice
  versionId: string
}) {
  const [cout, setCout] = useState<string | null>(null)
  const [incalculable, setIncalculable] = useState(false)

  useEffect(() => {
    let vivant = true
    setCout(null)
    setIncalculable(false)
    api
      .previewComposite(versionId, {
        unit_code: composite.unit_code,
        components: composite.components,
      })
      .then((rendu) => {
        if (vivant) setCout(`${rendu.unit_cost_display} ${rendu.currency}`)
      })
      .catch(() => vivant && setIncalculable(true))
    return () => {
      vivant = false
    }
  }, [versionId, composite])

  return (
    <span data-testid={`resume-${composite.code}`}>
      <span className="badge">{t('priceSource.composite')}</span>{' '}
      <span className="mono">{composite.code}</span> — {composite.label}{' '}
      <span className="muted">
        ({composite.components.length} {t('composites.components').toLowerCase()})
      </span>{' '}
      {incalculable ? (
        <span className="badge danger" data-testid={`incalculable-${composite.code}`}>
          {t('priceSource.notComputable')}
        </span>
      ) : (
        <span className="mono">{cout ?? '…'}</span>
      )}
    </span>
  )
}
