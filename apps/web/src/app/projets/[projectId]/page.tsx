'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'

import { ClientDuChantier } from '@/components/ClientDuChantier'
import { DocumentsDuProjet } from '@/components/DocumentsDuProjet'
import { ErrorNotice, Loading } from '@/components/Feedback'
import { Shell } from '@/components/Shell'
import {
  ResumeDuSousDetail,
  SourceDePrix,
  sourceDe,
  type Choix,
} from '@/components/SourceDePrix'
import {
  api,
  type Boq,
  type BoqItem,
  type Estimate,
  type EstimateVersion,
  type CompositePrice,
  type PriceItem,
  type Project,
} from '@/lib/api'
import { t } from '@/lib/i18n'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

type VersionsByEstimate = Record<string, EstimateVersion[]>

export default function ProjectPage() {
  const params = useParams<{ projectId: string }>()
  const projectId = params.projectId

  const [project, setProject] = useState<Project | null>(null)
  const [boqs, setBoqs] = useState<Boq[]>([])
  const [items, setItems] = useState<BoqItem[]>([])
  const [estimates, setEstimates] = useState<Estimate[]>([])
  const [versions, setVersions] = useState<VersionsByEstimate>({})
  const [error, setError] = useState<unknown>(null)
  const [ready, setReady] = useState(false)
  const [newItem, setNewItem] = useState({
    position: '',
    designation: '',
    unit_code: 'm3',
    quantity: '0',
    kind: 'item',
    // La SOURCE du prix vit dans `choixPrix` : une ligne peut tirer son prix
    // de la bibliothèque ou d'un sous-détail, et les deux s'excluent. Garder
    // ici le seul `price_item_id` rendait la seconde source insaisissable.
  })
  const [prix, setPrix] = useState<PriceItem[]>([])
  const [composites, setComposites] = useState<CompositePrice[]>([])
  const [versionsMelangees, setVersionsMelangees] = useState(false)
  const [choixPrix, setChoixPrix] = useState<Choix>({
    source: 'none',
    price_item_id: null,
    composite_price_id: null,
  })
  const permissions = usePermissions()
  const ecrireBordereau = can(permissions, PERMISSIONS.boqWrite)
  const ecrireEtude = can(permissions, PERMISSIONS.estimateWrite)
  const [versionPrix, setVersionPrix] = useState<string>('')

  const load = useCallback(async () => {
    try {
      const [loadedProject, loadedBoqs, loadedEstimates] = await Promise.all([
        api.project(projectId),
        api.boqs(projectId),
        api.estimates(projectId),
      ])
      setProject(loadedProject)
      setBoqs(loadedBoqs)
      setEstimates(loadedEstimates)
      const firstBoq = loadedBoqs[0]
      setItems(firstBoq ? await api.boqItems(firstBoq.id) : [])

      // La version de bibliothèque que les ESTIMATIONS de ce projet utilisent
      // réellement — et non la première venue.
      //
      // L'écran prenait la première version de la première bibliothèque. Un
      // poste pouvait donc recevoir un prix d'une version que l'étude
      // n'emploie pas : le devis se serait calculé sur autre chose que ce que
      // l'utilisateur avait choisi, sans qu'un seul écran le signale.
      //
      // Quand les estimations n'emploient pas toutes la même version, aucune
      // n'est retenue : proposer l'une d'elles reviendrait à choisir en
      // silence, et c'est exactement le mélange qu'on supprime.
      //
      // Dans son propre `try` : un lecteur n'a pas `pricebook:read`, et le
      // refus faisait échouer TOUT le chargement — la liste des études
      // disparaissait avec, et la page devenait illisible pour le rôle qui ne
      // fait précisément que lire. Un catalogue indisponible ne retire que le
      // sélecteur de prix.
      const versionsUtilisees = [...new Set(loadedEstimates.map((e) => e.price_book_version_id))]
      setVersionsMelangees(versionsUtilisees.length > 1)
      const versionUtilisee = versionsUtilisees.length === 1 ? versionsUtilisees[0] : undefined
      try {
        let cible = versionUtilisee
        if (!cible) {
          // Aucune estimation encore : on propose la version courante de la
          // première bibliothèque, celle qu'une nouvelle étude prendra.
          const livres = await api.priceBooks()
          const premier = livres[0]
          if (premier && versionsUtilisees.length === 0) {
            const versionsPrix = await api.priceBookVersions(premier.id)
            cible = versionsPrix[0]?.id
          }
        }
        if (cible) {
          setVersionPrix(cible)
          setPrix((await api.priceItems(cible, '?limit=200')).items)
          setComposites(await api.composites(cible))
        } else {
          setPrix([])
          setComposites([])
          setVersionPrix('')
        }
      } catch {
        setPrix([])
        setComposites([])
        setVersionPrix('')
      }
      const versionEntries = await Promise.all(
        loadedEstimates.map(async (estimate) => [estimate.id, await api.estimateVersions(estimate.id)] as const),
      )
      setVersions(Object.fromEntries(versionEntries))
    } catch (caught) {
      setError(caught)
    } finally {
      setReady(true)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  async function createBoq() {
    setError(null)
    try {
      await api.createBoq(projectId, { name: 'Métré interne', source: 'manual' })
      await load()
    } catch (caught) {
      setError(caught)
    }
  }

  async function createEstimate() {
    const boq = boqs[0]
    if (!boq || !versionPrix) return
    setError(null)
    try {
      // L'API crée l'estimation ET sa « Version initiale » : une estimation
      // sans version n'aurait rien à calculer ni à geler.
      await api.createEstimate({
        project_id: projectId,
        boq_id: boq.id,
        price_book_version_id: versionPrix,
        name: 'Étude de prix',
      })
      await load()
    } catch (caught) {
      setError(caught)
    }
  }

  async function addItem(event: React.FormEvent) {
    event.preventDefault()
    const boq = boqs[0]
    if (!boq) return
    setError(null)
    try {
      // `price_item_id` vide signifie « pas de prix » : l'API refuse la chaîne
      // vide, il faut envoyer null.
      await api.createBoqItem(boq.id, {
        ...newItem,
        // Les deux champs partent ENSEMBLE, l'un à `null`. L'API refuse leur
        // cumul ; les envoyer explicitement évite que ce refus atteigne
        // l'utilisateur pour une exclusivité qu'il croyait avoir respectée.
        price_item_id: choixPrix.price_item_id,
        composite_price_id: choixPrix.composite_price_id,
      })
      setNewItem({
        position: '',
        designation: '',
        unit_code: 'm3',
        quantity: '0',
        kind: 'item',
      })
      setChoixPrix({ source: 'none', price_item_id: null, composite_price_id: null })
      setItems(await api.boqItems(boq.id))
    } catch (caught) {
      setError(caught)
    }
  }

  if (!ready) {
    return (
      <Shell>
        <Loading />
      </Shell>
    )
  }

  return (
    <Shell>
      <h1>
        <span className="mono">{project?.reference}</span> — {project?.name}
      </h1>
      <p className="muted">
        {project?.client_name} · {project?.city} ·{' '}
        <span className="badge">
          {project?.country_code} / {project?.region_code}
        </span>
      </p>
      <ErrorNotice error={error} />

      {project && <ClientDuChantier project={project} onChange={setProject} />}

      <DocumentsDuProjet projectId={projectId} />

      <h2>{t('boq.title')}</h2>
      {boqs.length === 0 ? (
        <div className="card">
          <p className="muted">{t('boq.empty')}</p>
          {/*
            Une commande que le rôle ne pourra jamais exécuter est masquée :
            l'API la refuserait, et un bouton qui échoue n'apprend rien. La
            phrase, elle, reste — l'absence doit s'expliquer.
          */}
          {ecrireBordereau && (
            <button className="primary" onClick={createBoq}>
              {t('common.create')}
            </button>
          )}
        </div>
      ) : (
        <>
          <div className="card" style={{ padding: 0 }}>
            {versionsMelangees && (
              <div className="notice warning" data-testid="versions-melangees">
                {t('priceSource.mixedVersions')}
              </div>
            )}
            <table>
              <thead>
                <tr>
                  <th>{t('boq.position')}</th>
                  <th>{t('boq.designation')}</th>
                  <th>{t('common.unit')}</th>
                  <th className="num">{t('common.quantity')}</th>
                  <th>{t('common.status')}</th>
                  <th>Prix</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className={item.kind === 'section' ? 'section' : undefined}>
                    <td className="mono">{item.position}</td>
                    <td>
                      {item.designation}
                      {item.formula && (
                        <div className="muted mono" style={{ fontSize: 11 }}>
                          {item.formula}
                        </div>
                      )}
                    </td>
                    <td>{item.kind === 'section' ? '' : item.unit_code}</td>
                    <td className="num">{item.kind === 'section' ? '' : item.quantity}</td>
                    <td>
                      <span className={`badge ${item.status === 'approved' ? 'success' : ''}`}>
                        {item.status}
                      </span>
                    </td>
                    <td>
                      {item.kind === 'section' ? (
                        ''
                      ) : (
                        <PrixDuPoste
                          item={item}
                          prix={prix}
                          composites={composites}
                          versionId={versionPrix}
                          modifiable={ecrireBordereau && !versionsMelangees}
                          onChange={() => void load()}
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {ecrireBordereau && (
            <form className="card" onSubmit={addItem}>
              <h3 style={{ marginTop: 0 }}>{t('boq.addItem')}</h3>
              <div className="row">
                <div className="field">
                  <label htmlFor="position">{t('boq.position')}</label>
                  <input
                    id="position"
                    required
                    value={newItem.position}
                    onChange={(event) => setNewItem({ ...newItem, position: event.target.value })}
                  />
                </div>
                <div className="field" style={{ flex: '3 1 300px' }}>
                  <label htmlFor="designation">{t('boq.designation')}</label>
                  <input
                    id="designation"
                    required
                    value={newItem.designation}
                    onChange={(event) => setNewItem({ ...newItem, designation: event.target.value })}
                  />
                </div>
                <div className="field">
                  <label htmlFor="unit">{t('common.unit')}</label>
                  <input
                    id="unit"
                    required
                    value={newItem.unit_code}
                    onChange={(event) => setNewItem({ ...newItem, unit_code: event.target.value })}
                  />
                </div>
                <div className="field">
                  <label htmlFor="quantity">{t('common.quantity')}</label>
                  <input
                    id="quantity"
                    inputMode="decimal"
                    value={newItem.quantity}
                    onChange={(event) => setNewItem({ ...newItem, quantity: event.target.value })}
                  />
                </div>
                <SourceDePrix
                  identifiant="nouveau"
                  choix={choixPrix}
                  prix={prix}
                  composites={composites}
                  onChange={setChoixPrix}
                />
                <div className="field">
                  <label htmlFor="kind">Type</label>
                  <select
                    id="kind"
                    value={newItem.kind}
                    onChange={(event) => setNewItem({ ...newItem, kind: event.target.value })}
                  >
                    <option value="item">Poste</option>
                    <option value="section">Chapitre</option>
                    <option value="option">Option</option>
                    <option value="variant">Variante</option>
                    <option value="provisional">Quantité présumée</option>
                  </select>
                </div>
              </div>
              <button className="primary" type="submit">
                {t('common.create')}
              </button>
            </form>
          )}
        </>
      )}

      <h2>{t('estimate.title')}</h2>
      {estimates.length === 0 ? (
        <div className="card">
          <p className="muted">
            Aucune étude de prix pour ce projet. Une étude reprend les lignes du bordereau et les
            chiffre avec une version de bibliothèque de prix.
          </p>
          {/*
            Ce qui manque, et l'action qui y remédie — mais seulement pour qui
            peut la faire. Dire « créez-en une » à un lecteur l'enverrait sur un
            refus.
          */}
          {!ecrireEtude ? null : boqs.length === 0 ? (
            <div className="notice warning" role="status">
              Créez d&apos;abord le bordereau ci-dessus : c&apos;est lui que l&apos;étude chiffre.
            </div>
          ) : !versionPrix ? (
            <div className="notice warning" role="status">
              Aucune bibliothèque de prix. <Link href="/bibliotheque">Créez-en une</Link> : une
              étude doit s&apos;appuyer sur une version de bibliothèque.
            </div>
          ) : (
            <button className="primary" onClick={() => void createEstimate()}>
              Créer une étude de prix
            </button>
          )}
        </div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Estimation</th>
                <th>{t('estimate.version')}</th>
                <th>{t('common.status')}</th>
                <th className="num">{t('estimate.totalHT')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {estimates.flatMap((estimate) =>
                (versions[estimate.id] ?? []).map((version) => (
                  <tr key={version.id}>
                    <td>{estimate.name}</td>
                    <td>v{version.version_number}</td>
                    <td>
                      <span className={`badge ${version.status === 'frozen' ? 'success' : ''}`}>
                        {version.status === 'frozen' ? t('estimate.frozen') : t('estimate.draft')}
                      </span>
                    </td>
                    {/*
                      Le total du document, ou une absence explicite. Cette
                      colonne affichait l'arrondi du total brut, qui diffère du
                      devis de quelques centimes : deux nombres pour une même
                      version. Une version gelée ancienne dont le total imprimé
                      n'est pas reconstructible affiche « — » et le dit au
                      survol, plutôt qu'un montant approchant.
                    */}
                    <td
                      className="num"
                      title={
                        version.status === 'frozen' && !version.document_totals_available
                          ? t('estimate.documentTotalUnknown')
                          : undefined
                      }
                    >
                      {version.total_selling_price_ht_display ?? t('common.none')}
                    </td>
                    <td>
                      <Link href={`/estimations/${estimate.id}/${version.id}`}>Ouvrir</Link>
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      )}
    </Shell>
  )
}


/**
 * Le prix d'un poste : ce qu'il est, et comment en changer la SOURCE.
 *
 * L'écran n'affichait qu'un badge en lecture. Un poste dont le prix venait
 * d'un sous-détail ne montrait ni son code, ni son coût, ni combien de
 * composants le composaient — et surtout, rien ne permettait de lui en
 * affecter un. Les deux sources sont maintenant offertes, exclusives, et le
 * sous-détail affiche son coût prévisualisé PAR LE SERVEUR.
 */
function PrixDuPoste({
  item,
  prix,
  composites,
  versionId,
  modifiable,
  onChange,
}: {
  item: BoqItem
  prix: PriceItem[]
  composites: CompositePrice[]
  versionId: string
  modifiable: boolean
  onChange: () => void
}) {
  const [edite, setEdite] = useState(false)
  const [choix, setChoix] = useState<Choix>(() => sourceDe(item))
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)
  const composite = composites.find((c) => c.id === item.composite_price_id)

  async function enregistrer() {
    setOccupe(true)
    setErreur(null)
    try {
      // Les DEUX champs partent, l'un à `null` : basculer de source efface
      // l'autre côté dans le même geste, sinon le poste garderait un
      // identifiant devenu invisible à l'écran.
      await api.updateBoqItem(item.id, {
        price_item_id: choix.price_item_id,
        composite_price_id: choix.composite_price_id,
      })
      setEdite(false)
      onChange()
    } catch (attrape) {
      setErreur(attrape)
    } finally {
      setOccupe(false)
    }
  }

  if (edite) {
    return (
      <div data-testid={`source-poste-${item.position}`}>
        <ErrorNotice error={erreur} />
        <SourceDePrix
          identifiant={item.id}
          choix={choix}
          prix={prix}
          composites={composites}
          onChange={setChoix}
        />
        <p>
          <button className="primary" onClick={() => void enregistrer()} disabled={occupe}>
            {t('common.save')}
          </button>{' '}
          <button onClick={() => setEdite(false)}>{t('common.cancel')}</button>
        </p>
      </div>
    )
  }

  return (
    <div data-testid={`prix-poste-${item.position}`}>
      {composite ? (
        <ResumeDuSousDetail composite={composite} versionId={versionId} />
      ) : item.composite_price_id ? (
        // Référencé mais introuvable dans la version chargée : le dire plutôt
        // que d'afficher un blanc que l'on prendrait pour « sans prix ».
        <span className="badge danger">{t('priceSource.otherVersion')}</span>
      ) : item.price_item_id ? (
        <span className="badge">{t('priceSource.library')}</span>
      ) : (
        <span className="badge danger">{t('estimate.missingPrice')}</span>
      )}{' '}
      {modifiable && (
        <button
          onClick={() => {
            setChoix(sourceDe(item))
            setEdite(true)
          }}
        >
          {t('priceSource.change')}
        </button>
      )}
    </div>
  )
}
