'use client'

import { useCallback, useEffect, useState } from 'react'

import { ErrorNotice } from '@/components/Feedback'
import {
  api,
  type Composant,
  type CompositePreview,
  type CompositePrice,
  type TypeDeComposant,
} from '@/lib/api'
import { t } from '@/lib/i18n'

/**
 * Le constructeur d'un sous-détail de prix.
 *
 * Trois principes, et chacun corrige une manière de se tromper :
 *
 * 1. **Aucun calcul ici.** Le coût unitaire et sa ventilation viennent de
 *    `POST …/composites/preview`, qui appelle le même moteur que l'estimation.
 *    Refaire l'arithmétique en TypeScript donnerait deux vérités qui divergent
 *    au premier arrondi, et c'est l'écran qui aurait tort.
 * 2. **Seuls les champs du type choisi sont montrés.** Une consommation n'a
 *    pas de taille d'équipe ; afficher les vingt champs pour laisser
 *    l'utilisateur deviner lesquels comptent, c'est lui faire porter la
 *    structure du modèle.
 * 3. **Aucune masse volumique par défaut.** Une densité inventée change le
 *    nombre de camions, donc le prix, sans que rien ne le signale. Le champ
 *    reste vide, sa source est exigée à côté, et l'écran dit pourquoi.
 */

type Brouillon = {
  code: string
  label: string
  unit_code: string
  notes: string
  components: Composant[]
}

/** Les champs de chaque type, dans l'ordre où on les saisit. */
const CHAMPS: Record<TypeDeComposant, { nom: string; cle: string; aide?: string }[]> = {
  consumption: [
    { nom: 'Quantité consommée', cle: 'consumption' },
    { nom: 'Unité de la ressource', cle: 'resource_unit_code' },
    { nom: 'Prix unitaire', cle: 'unit_price' },
    { nom: 'Perte (0,05 = 5 %)', cle: 'loss_ratio' },
  ],
  output_rate: [
    { nom: 'Rendement horaire', cle: 'output_rate', aide: 'quantité produite en une heure' },
    { nom: 'Taux horaire', cle: 'hourly_rate' },
    { nom: "Taille de l'équipe", cle: 'crew_size' },
  ],
  rotation: [
    { nom: 'Charge utile', cle: 'payload_value' },
    { nom: 'Unité de charge', cle: 'payload_unit_code' },
    { nom: 'Coût par rotation', cle: 'cost_per_rotation' },
    { nom: 'Distance (km)', cle: 'distance_km' },
    { nom: 'Tarif au km', cle: 'rate_per_km' },
  ],
  lump_sum: [{ nom: 'Montant', cle: 'lump_sum_amount' }],
}

/** Les types qui peuvent croiser volume et masse, donc exiger une densité. */
const CONVERTISSENT: TypeDeComposant[] = ['consumption', 'rotation']

const NATURES = [
  ['material', 'Matériaux'],
  ['labor', "Main-d'œuvre"],
  ['equipment', 'Engins'],
  ['transport', 'Transport'],
  ['disposal', 'Évacuation / traitement'],
  ['subcontract', 'Sous-traitance'],
  ['other', 'Divers'],
] as const

function composantVide(type: TypeDeComposant): Composant {
  return { component_type: type, label: '', resource_kind: 'material' }
}

/** Ce que le serveur a refusé, rangé par index de composant puis par champ. */
type Refus = { global: string[]; parComposant: Record<number, string[]> }

function lireLesRefus(erreur: unknown): Refus {
  const vide: Refus = { global: [], parComposant: {} }
  const detail = (erreur as { detail?: unknown })?.detail
  if (Array.isArray(detail)) {
    // Refus de schéma : `loc` porte l'index du composant et le champ.
    for (const entree of detail as { loc?: unknown[]; msg?: string }[]) {
      const loc = entree.loc ?? []
      const index = loc.find((part) => typeof part === 'number')
      const champ = loc.filter((part) => typeof part === 'string').slice(-1)[0]
      const message = `${champ ? `${champ} : ` : ''}${entree.msg ?? ''}`
      if (typeof index === 'number') {
        vide.parComposant[index] = [...(vide.parComposant[index] ?? []), message]
      } else {
        vide.global.push(message)
      }
    }
    return vide
  }
  const objet = detail as { problems?: { index: number; message: string }[]; message?: string }
  if (objet?.problems) {
    for (const probleme of objet.problems) {
      vide.parComposant[probleme.index] = [
        ...(vide.parComposant[probleme.index] ?? []),
        probleme.message,
      ]
    }
    return vide
  }
  if (objet?.message) vide.global.push(objet.message)
  return vide
}

export function EditeurSousDetail({
  versionId,
  existant,
  onEnregistre,
  onAnnule,
}: {
  versionId: string
  existant: CompositePrice | null
  onEnregistre: () => void
  onAnnule: () => void
}) {
  const [brouillon, setBrouillon] = useState<Brouillon>(() =>
    existant
      ? {
          code: existant.code,
          label: existant.label,
          unit_code: existant.unit_code,
          notes: existant.notes ?? '',
          components: existant.components.map((c) => ({ ...c })),
        }
      : {
          code: '',
          label: '',
          unit_code: '',
          notes: '',
          components: [composantVide('consumption')],
        },
  )
  const [apercu, setApercu] = useState<CompositePreview | null>(null)
  const [refus, setRefus] = useState<Refus>({ global: [], parComposant: {} })
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)

  // L'aperçu suit la saisie, avec un temps mort : une requête par frappe
  // saturerait le serveur pour un chiffre que personne ne lit à cette vitesse.
  const sonder = useCallback(async () => {
    if (!brouillon.unit_code || brouillon.components.length === 0) {
      setApercu(null)
      return
    }
    try {
      setApercu(
        await api.previewComposite(versionId, {
          unit_code: brouillon.unit_code,
          components: brouillon.components,
        }),
      )
      setRefus({ global: [], parComposant: {} })
    } catch (attrape) {
      setApercu(null)
      setRefus(lireLesRefus(attrape))
    }
  }, [versionId, brouillon.unit_code, brouillon.components])

  useEffect(() => {
    const minuteur = setTimeout(() => void sonder(), 400)
    return () => clearTimeout(minuteur)
  }, [sonder])

  function modifierComposant(index: number, champ: string, valeur: unknown) {
    const suivants = brouillon.components.map((c, i) =>
      i === index ? { ...c, [champ]: valeur } : c,
    )
    setBrouillon({ ...brouillon, components: suivants })
  }

  function changerLeType(index: number, type: TypeDeComposant) {
    // Le type change : on repart des seuls champs communs. Garder les anciens
    // enverrait au serveur des valeurs qui n'ont plus de sens pour ce type, et
    // le refus porterait sur une saisie que l'utilisateur ne voit plus.
    const ancien = brouillon.components[index]
    const suivants = brouillon.components.map((c, i) =>
      i === index
        ? { component_type: type, label: ancien?.label ?? '', resource_kind: ancien?.resource_kind ?? 'material' }
        : c,
    )
    setBrouillon({ ...brouillon, components: suivants })
  }

  function deplacer(index: number, pas: number) {
    const cible = index + pas
    if (cible < 0 || cible >= brouillon.components.length) return
    const suivants = [...brouillon.components]
    const [retire] = suivants.splice(index, 1)
    if (retire) suivants.splice(cible, 0, retire)
    setBrouillon({ ...brouillon, components: suivants })
  }

  function dupliquerComposant(index: number) {
    const source = brouillon.components[index]
    if (!source) return
    const suivants = [...brouillon.components]
    suivants.splice(index + 1, 0, { ...source, label: `${source.label} (copie)` })
    setBrouillon({ ...brouillon, components: suivants })
  }

  async function enregistrer() {
    setOccupe(true)
    setErreur(null)
    setRefus({ global: [], parComposant: {} })
    const corps = {
      code: brouillon.code,
      label: brouillon.label,
      unit_code: brouillon.unit_code,
      notes: brouillon.notes || null,
      components: brouillon.components,
    }
    try {
      if (existant) {
        await api.updateComposite(existant.id, { ...corps, revision: existant.revision })
      } else {
        await api.createComposite(versionId, corps)
      }
      onEnregistre()
    } catch (attrape) {
      const lus = lireLesRefus(attrape)
      if (lus.global.length || Object.keys(lus.parComposant).length) setRefus(lus)
      else setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  return (
    <div className="card" data-testid="editeur-sous-detail">
      <h3 style={{ marginTop: 0 }}>
        {existant ? t('composites.editTitle') : t('composites.newTitle')}
      </h3>
      <ErrorNotice error={erreur} />
      {refus.global.map((message) => (
        <div className="notice warning" role="status" key={message}>
          {message}
        </div>
      ))}

      <div className="row">
        <div className="field">
          <label htmlFor="sd-code">{t('composites.code')}</label>
          <input
            id="sd-code"
            className="mono"
            value={brouillon.code}
            onChange={(e) => setBrouillon({ ...brouillon, code: e.target.value })}
          />
        </div>
        <div className="field" style={{ flex: '2 1 300px' }}>
          <label htmlFor="sd-label">{t('composites.label')}</label>
          <input
            id="sd-label"
            value={brouillon.label}
            onChange={(e) => setBrouillon({ ...brouillon, label: e.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor="sd-unit">{t('composites.unit')}</label>
          <input
            id="sd-unit"
            className="mono"
            value={brouillon.unit_code}
            onChange={(e) => setBrouillon({ ...brouillon, unit_code: e.target.value })}
          />
        </div>
      </div>

      <h4>{t('composites.components')}</h4>
      {brouillon.components.map((composant, index) => (
        <CarteComposant
          key={index}
          index={index}
          composant={composant}
          erreurs={refus.parComposant[index] ?? []}
          dernier={index === brouillon.components.length - 1}
          onChamp={(champ, valeur) => modifierComposant(index, champ, valeur)}
          onType={(type) => changerLeType(index, type)}
          onDeplacer={(pas) => deplacer(index, pas)}
          onDupliquer={() => dupliquerComposant(index)}
          onRetirer={() =>
            setBrouillon({
              ...brouillon,
              components: brouillon.components.filter((_, i) => i !== index),
            })
          }
        />
      ))}

      <p>
        <button
          onClick={() =>
            setBrouillon({
              ...brouillon,
              components: [...brouillon.components, composantVide('consumption')],
            })
          }
        >
          {t('composites.addComponent')}
        </button>
      </p>

      <Apercu apercu={apercu} />

      <p>
        <button className="primary" onClick={() => void enregistrer()} disabled={occupe}>
          {t('common.save')}
        </button>{' '}
        <button onClick={onAnnule}>{t('common.cancel')}</button>
      </p>
    </div>
  )
}

function CarteComposant({
  index,
  composant,
  erreurs,
  dernier,
  onChamp,
  onType,
  onDeplacer,
  onDupliquer,
  onRetirer,
}: {
  index: number
  composant: Composant
  erreurs: string[]
  dernier: boolean
  onChamp: (champ: string, valeur: unknown) => void
  onType: (type: TypeDeComposant) => void
  onDeplacer: (pas: number) => void
  onDupliquer: () => void
  onRetirer: () => void
}) {
  const type = composant.component_type
  const convertit = CONVERTISSENT.includes(type)
  return (
    <div
      className="card"
      data-testid={`composant-${index}`}
      style={{ marginBottom: '0.75rem' }}
    >
      <div className="row">
        <div className="field">
          <label htmlFor={`type-${index}`}>{t('composites.componentType')}</label>
          <select
            id={`type-${index}`}
            value={type}
            onChange={(e) => onType(e.target.value as TypeDeComposant)}
          >
            <option value="consumption">{t('composites.type.consumption')}</option>
            <option value="output_rate">{t('composites.type.output_rate')}</option>
            <option value="rotation">{t('composites.type.rotation')}</option>
            <option value="lump_sum">{t('composites.type.lump_sum')}</option>
          </select>
        </div>
        <div className="field" style={{ flex: '2 1 260px' }}>
          <label htmlFor={`label-${index}`}>{t('composites.componentLabel')}</label>
          <input
            id={`label-${index}`}
            value={composant.label}
            onChange={(e) => onChamp('label', e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`kind-${index}`}>{t('composites.componentKind')}</label>
          <select
            id={`kind-${index}`}
            value={composant.resource_kind}
            onChange={(e) => onChamp('resource_kind', e.target.value)}
          >
            {NATURES.map(([valeur, libelle]) => (
              <option key={valeur} value={valeur}>
                {libelle}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="row">
        {CHAMPS[type].map((champ) => (
          <div className="field" key={champ.cle}>
            <label htmlFor={`${champ.cle}-${index}`}>{champ.nom}</label>
            <input
              id={`${champ.cle}-${index}`}
              className="mono"
              value={String(composant[champ.cle] ?? '')}
              onChange={(e) => onChamp(champ.cle, e.target.value || null)}
            />
            {champ.aide && <small className="muted">{champ.aide}</small>}
          </div>
        ))}
      </div>

      {convertit && (
        <div className="row">
          <div className="field">
            <label htmlFor={`density-${index}`}>{t('composites.density')}</label>
            <input
              id={`density-${index}`}
              className="mono"
              // Aucun `placeholder` chiffré : il se lirait comme une valeur
              // usuelle, et une densité supposée fausse tout un métré.
              value={String(composant.density_value ?? '')}
              onChange={(e) => onChamp('density_value', e.target.value || null)}
            />
          </div>
          <div className="field" style={{ flex: '2 1 300px' }}>
            <label htmlFor={`densitysrc-${index}`}>{t('composites.densitySource')}</label>
            <input
              id={`densitysrc-${index}`}
              value={String(composant.density_source ?? '')}
              onChange={(e) => onChamp('density_source', e.target.value || null)}
            />
          </div>
        </div>
      )}
      {convertit && <p className="muted">{t('composites.densityWhy')}</p>}

      {erreurs.map((message) => (
        <div className="notice warning" role="status" key={message}>
          {message}
        </div>
      ))}

      <p>
        <button onClick={() => onDeplacer(-1)} disabled={index === 0}>
          {t('composites.moveUp')}
        </button>{' '}
        <button onClick={() => onDeplacer(1)} disabled={dernier}>
          {t('composites.moveDown')}
        </button>{' '}
        <button onClick={onDupliquer}>{t('composites.duplicateComponent')}</button>{' '}
        <button onClick={onRetirer}>{t('composites.removeComponent')}</button>
      </p>
    </div>
  )
}

/** Le chiffre du SERVEUR, et sa ventilation. Jamais recalculé ici. */
function Apercu({ apercu }: { apercu: CompositePreview | null }) {
  if (!apercu) {
    return (
      <p className="muted" data-testid="apercu-indisponible">
        {t('composites.previewUnavailable')}
      </p>
    )
  }
  return (
    <div data-testid="apercu-cout">
      <p>
        <strong>{t('composites.unitCost')}</strong>{' '}
        <span className="mono" data-testid="cout-unitaire">
          {apercu.unit_cost_display} {apercu.currency} / {apercu.unit_code}
        </span>
      </p>
      {!apercu.scales_linearly && (
        <div className="notice warning" data-testid="non-proportionnel">
          {t('composites.notLinear')}
        </div>
      )}
      <table data-testid="ventilation">
        <thead>
          <tr>
            <th>{t('composites.kind')}</th>
            <th className="num">{t('composites.amount')}</th>
          </tr>
        </thead>
        <tbody>
          {apercu.by_kind.map((nature) => (
            <tr key={nature.resource_kind}>
              <td>{nature.label}</td>
              <td className="num mono">
                {nature.amount_display} {apercu.currency}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
