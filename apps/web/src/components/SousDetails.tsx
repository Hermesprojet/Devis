'use client'

import { useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import { api, type CompositePrice } from '@/lib/api'
import { t } from '@/lib/i18n'

/**
 * Les sous-détails d'une version de bibliothèque, en lecture.
 *
 * Ils existaient déjà — le jeu de démonstration en sème deux, l'API les crée
 * et les liste — mais **aucun écran ne les montrait**. Un métreur pouvait donc
 * chiffrer une ligne avec un sous-détail sans jamais voir de quoi il était
 * fait, alors que la décomposition est précisément ce que ce produit promet.
 *
 * En lecture seule, et c'est délibéré : l'API n'expose ni modification ni
 * suppression d'un sous-détail. Proposer un bouton qui échouerait serait
 * exactement ce que les règles de revue interdisent.
 */
export function SousDetails({ versionId }: { versionId: string }) {
  const [composites, setComposites] = useState<CompositePrice[]>([])
  const [ouvert, setOuvert] = useState<string | null>(null)
  const [erreur, setErreur] = useState<unknown>(null)
  const [charge, setCharge] = useState(false)

  useEffect(() => {
    let vivant = true
    if (!versionId) {
      setComposites([])
      setCharge(true)
      return
    }
    setCharge(false)
    api
      .composites(versionId)
      .then((liste) => {
        if (vivant) {
          setComposites(liste)
          setErreur(null)
        }
      })
      .catch((attrape) => vivant && setErreur(attrape))
      .finally(() => vivant && setCharge(true))
    return () => {
      vivant = false
    }
  }, [versionId])

  return (
    <div className="card" data-testid="sous-details">
      <h2 style={{ marginTop: 0 }}>{t('composites.title')}</h2>
      <p className="muted">{t('composites.hint')}</p>
      <ErrorNotice error={erreur} />

      {charge && composites.length === 0 && !erreur && (
        <p className="muted" data-testid="sous-details-vide">
          {t('composites.empty')}
        </p>
      )}

      {composites.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t('composites.code')}</th>
              <th>{t('composites.label')}</th>
              <th>{t('composites.unit')}</th>
              <th>{t('composites.components')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {composites.map((composite) => (
              <Ligne
                key={composite.id}
                composite={composite}
                ouvert={ouvert === composite.id}
                onBascule={() => setOuvert(ouvert === composite.id ? null : composite.id)}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function Ligne({
  composite,
  ouvert,
  onBascule,
}: {
  composite: CompositePrice
  ouvert: boolean
  onBascule: () => void
}) {
  return (
    <>
      <tr data-testid={`sous-detail-${composite.code}`}>
        <td className="mono">{composite.code}</td>
        <td>
          {composite.label}
          {composite.is_demo_data && (
            <>
              {' '}
              <span className="badge warning">{t('priceBook.demoFlag')}</span>
            </>
          )}
        </td>
        <td className="mono">{composite.unit_code}</td>
        <td className="mono">{composite.components.length}</td>
        <td>
          <button onClick={onBascule} aria-expanded={ouvert}>
            {ouvert ? t('composites.hide') : t('composites.show')}
          </button>
        </td>
      </tr>
      {ouvert && (
        <tr>
          <td colSpan={5}>
            <Composants composants={composite.components} />
            {composite.notes && <p className="muted">{composite.notes}</p>}
          </td>
        </tr>
      )}
    </>
  )
}

/**
 * Les composants d'un sous-détail.
 *
 * Chaque type porte des champs différents — une consommation a un ratio de
 * perte, un rendement a une taille d'équipe, une rotation a une charge utile.
 * Plutôt que quatre tableaux, un seul qui affiche **ce qui est présent** : la
 * forme vient du serveur, l'écran ne la réinvente pas. Une clé nouvelle
 * apparaîtra donc d'elle-même au lieu d'être silencieusement ignorée.
 */
function Composants({ composants }: { composants: Record<string, unknown>[] }) {
  if (composants.length === 0) {
    return <p className="muted">{t('composites.noComponent')}</p>
  }
  return (
    <table data-testid="composants">
      <thead>
        <tr>
          <th>{t('composites.componentType')}</th>
          <th>{t('composites.componentLabel')}</th>
          <th>{t('composites.componentKind')}</th>
          <th>{t('composites.componentDetail')}</th>
        </tr>
      </thead>
      <tbody>
        {composants.map((composant, rang) => (
          <tr key={rang}>
            <td className="mono">{t(`composites.type.${String(composant.component_type)}`)}</td>
            <td>{String(composant.label ?? '')}</td>
            <td className="mono">{String(composant.resource_kind ?? '')}</td>
            <td className="mono">{detail(composant)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * Les champs propres au type, lisibles.
 *
 * Les libellés connus sont traduits ; un champ que ce fichier ne connaît pas
 * s'affiche sous son nom technique **plutôt que de disparaître**. C'est le
 * compromis voulu : une table de traduction exhaustive se périme au premier
 * champ ajouté côté serveur, et un écran qui filtre sur une liste blanche
 * cache alors une information sans le dire.
 */
const CHAMPS: Record<string, string> = {
  consumption: 'consommation',
  resource_unit_code: 'unité',
  unit_price: 'prix unitaire',
  loss_ratio: 'perte',
  convert_boq_quantity: 'conversion depuis le bordereau',
  density_value: 'densité',
  density_source: 'source de la densité',
  output_rate: 'rendement',
  hourly_rate: 'taux horaire',
  crew_size: 'équipe',
  payload_value: 'charge utile',
  payload_unit_code: 'unité de charge',
  cost_per_rotation: 'coût par rotation',
  round_up: 'rotations arrondies au supérieur',
  distance_km: 'distance (km)',
  amount_value: 'montant',
  amount_unit_code: 'devise',
}

function detail(composant: Record<string, unknown>): string {
  // `component_type`, `label` et `resource_kind` ont déjà leur colonne : les
  // répéter ici ne dirait rien de plus.
  const communs = new Set(['component_type', 'label', 'resource_kind'])
  return Object.entries(composant)
    .filter(([cle, valeur]) => !communs.has(cle) && valeur !== null && valeur !== undefined)
    .map(([cle, valeur]) => `${CHAMPS[cle] ?? cle} ${String(valeur)}`)
    .join(' · ')
}
