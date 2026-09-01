'use client'

import { useCallback, useEffect, useState } from 'react'

import { EditeurSousDetail } from '@/components/EditeurSousDetail'
import { ErrorNotice } from '@/components/Feedback'
import { api, type CompositePrice } from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * Les sous-détails d'une version de bibliothèque : lire, créer, modifier,
 * dupliquer, supprimer.
 *
 * Mesuré au navigateur depuis une organisation vide : créer une bibliothèque
 * marchait, construire un prix composé non. Les sous-détails existaient — le
 * jeu de démonstration en sème deux, l'API savait les créer et les lister —
 * mais aucun écran ne les montrait ni ne les reprenait. Un métreur chiffrait
 * une ligne avec un sous-détail sans jamais voir de quoi il était fait.
 *
 * **Aucune commande qui échouerait n'est offerte.** `version_published` et
 * `referenced_by` viennent du serveur avec chaque ligne ; l'écran les lit au
 * lieu de les redeviner. Une version publiée est en lecture seule, et le dit ;
 * un sous-détail utilisé par des postes ne propose pas de suppression.
 */
export function SousDetails({ versionId }: { versionId: string }) {
  const [composites, setComposites] = useState<CompositePrice[]>([])
  const [recherche, setRecherche] = useState('')
  const [ouvert, setOuvert] = useState<string | null>(null)
  const [edite, setEdite] = useState<CompositePrice | null>(null)
  const [creation, setCreation] = useState(false)
  const [erreur, setErreur] = useState<unknown>(null)
  const [charge, setCharge] = useState(false)
  const permissions = usePermissions()
  const ecrire = can(permissions, PERMISSIONS.pricebookWrite)

  const recharger = useCallback(async () => {
    if (!versionId) {
      setComposites([])
      setCharge(true)
      return
    }
    try {
      setComposites(await api.composites(versionId))
      setErreur(null)
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setCharge(true)
    }
  }, [versionId])

  useEffect(() => {
    void recharger()
  }, [recharger])

  const publiee = composites.some((c) => c.version_published)
  const modifiable = ecrire && !publiee

  const terme = recherche.trim().toLowerCase()
  const visibles = terme
    ? composites.filter(
        (c) => c.code.toLowerCase().includes(terme) || c.label.toLowerCase().includes(terme),
      )
    : composites

  async function supprimer(composite: CompositePrice) {
    setErreur(null)
    try {
      await api.deleteComposite(composite.id)
      await recharger()
    } catch (attrape) {
      setErreur(attrape)
    }
  }

  async function dupliquer(composite: CompositePrice) {
    setErreur(null)
    // Le code est demandé plutôt que dérivé : un suffixe automatique produit
    // des noms que personne ne relit, et deux duplications successives donnent
    // un code qui ne dit plus rien.
    const code = window.prompt(t('composites.duplicatePrompt'), `${composite.code}-2`)
    if (!code) return
    try {
      await api.duplicateComposite(composite.id, { code })
      await recharger()
    } catch (attrape) {
      setErreur(attrape)
    }
  }

  if (creation || edite) {
    return (
      <EditeurSousDetail
        versionId={versionId}
        existant={edite}
        onEnregistre={() => {
          setCreation(false)
          setEdite(null)
          void recharger()
        }}
        onAnnule={() => {
          setCreation(false)
          setEdite(null)
        }}
      />
    )
  }

  return (
    <div className="card" data-testid="sous-details">
      <h2 style={{ marginTop: 0 }}>{t('composites.title')}</h2>
      <p className="muted">{t('composites.hint')}</p>
      <ErrorNotice error={erreur} />

      {publiee && (
        <div className="notice info" data-testid="version-publiee">
          {t('composites.publishedReadOnly')}
        </div>
      )}

      <div className="row">
        <div className="field" style={{ flex: '2 1 300px' }}>
          <label htmlFor="sd-recherche">{t('composites.search')}</label>
          <input
            id="sd-recherche"
            value={recherche}
            onChange={(evenement) => setRecherche(evenement.target.value)}
          />
        </div>
      </div>

      {modifiable && (
        <p>
          <button className="primary" onClick={() => setCreation(true)}>
            {t('composites.new')}
          </button>
        </p>
      )}

      {charge && composites.length === 0 && !erreur && (
        <p className="muted" data-testid="sous-details-vide">
          {t('composites.empty')}
        </p>
      )}

      {visibles.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t('composites.code')}</th>
              <th>{t('composites.label')}</th>
              <th>{t('composites.unit')}</th>
              <th className="num">{t('composites.components')}</th>
              <th className="num">{t('composites.usedBy')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {visibles.map((composite) => (
              <Ligne
                key={composite.id}
                composite={composite}
                ouvert={ouvert === composite.id}
                modifiable={modifiable}
                onBascule={() => setOuvert(ouvert === composite.id ? null : composite.id)}
                onModifier={() => setEdite(composite)}
                onDupliquer={() => void dupliquer(composite)}
                onSupprimer={() => void supprimer(composite)}
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
  modifiable,
  onBascule,
  onModifier,
  onDupliquer,
  onSupprimer,
}: {
  composite: CompositePrice
  ouvert: boolean
  modifiable: boolean
  onBascule: () => void
  onModifier: () => void
  onDupliquer: () => void
  onSupprimer: () => void
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
        <td className="num mono">{composite.components.length}</td>
        <td className="num mono">{composite.referenced_by}</td>
        <td>
          <button onClick={onBascule} aria-expanded={ouvert}>
            {ouvert ? t('composites.hide') : t('composites.show')}
          </button>{' '}
          {modifiable && (
            <>
              <button onClick={onModifier}>{t('common.edit')}</button>{' '}
              <button onClick={onDupliquer}>{t('composites.duplicate')}</button>{' '}
              {/* Un sous-détail utilisé ne propose pas de suppression : l'API
                  la refuserait en 409, et offrir une commande qui échoue est
                  précisément ce que les règles de revue interdisent. */}
              {composite.referenced_by === 0 && (
                <button onClick={onSupprimer}>{t('common.delete')}</button>
              )}
            </>
          )}
        </td>
      </tr>
      {ouvert && (
        <tr>
          <td colSpan={6}>
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
 * Chaque type porte des champs différents. Plutôt que quatre tableaux, un seul
 * qui affiche **ce qui est présent** : la forme vient du serveur, l'écran ne
 * la réinvente pas. Un champ que ce fichier ne connaît pas encore apparaît
 * sous son nom technique au lieu d'être silencieusement ignoré.
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
 * s'affiche sous son nom technique **plutôt que de disparaître**. Une table de
 * traduction exhaustive se périme au premier champ ajouté côté serveur, et un
 * écran qui filtrerait sur une liste blanche cacherait alors une information
 * sans le dire.
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
  rate_per_km: 'tarif au km',
  amount_value: 'montant',
  amount_unit_code: 'devise',
  lump_sum_amount: 'montant',
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
