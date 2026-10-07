'use client'

/**
 * L'écran d'un plan PDF : le voir, le calibrer, le mesurer, le confirmer.
 *
 * Composant distinct de `LecturePlan`, qui sert les DXF, et ce n'est pas un
 * doublon : les deux formats ne demandent pas le même geste. Un DXF porte ses
 * cotations et son unité ; on les LIT. Un PDF ne porte rien de tel ; on y
 * POINTE. Fondre les deux dans un composant produirait un écran dont la moitié
 * des commandes serait désactivée selon le format.
 *
 * **Les trois chiffres mesurés qui dictent cette interface.**
 *
 * 1. Sur l'aperçu pleine page, un pixel vaut **12 à 42 millimètres d'ouvrage**
 *    (quatre plans réels de grand format, 2 000 px de grand côté). On ne peut donc pas y
 *    pointer utilement : l'aperçu sert à TROUVER, la tuile agrandie à POINTER.
 * 2. La hauteur médiane d'une ligne de texte y est de **2,4 à 3,6 pixels**,
 *    celle d'un caractère de 2,1 à 2,5. On ne peut pas non plus y relire une
 *    cote.
 * 3. Une tuile coûte **0,2 à 5,3 secondes** la première fois, puis **0,3 ms**.
 *    Elle se demande donc explicitement, et jamais au fil de la souris.
 *
 * D'où le parcours : on navigue sur l'aperçu, on ouvre une loupe sur la zone
 * qui intéresse, et c'est DANS la loupe qu'on calibre et qu'on mesure.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  api,
  type CalibrationDePlan,
  type FragmentDeTexte,
  type MesureDePdf,
  type MesuresDePdf,
  type PlanLu,
  type PointDEcran,
  type TextesDePlan,
} from '@/lib/api'
import { t } from '@/lib/i18n'
import { ErrorNotice } from '@/components/Feedback'

/** Les unités de longueur que la calibration accepte, dans l'ordre d'usage. */
const UNITES = ['mm', 'cm', 'm'] as const

/**
 * La taille d'une loupe, en fraction de la page.
 *
 * Un vingtième de la largeur : sur un A0 de 1 189 mm, cela fait 59 mm de
 * papier, soit environ 3 mètres d'ouvrage au 1:50. C'est l'ordre de grandeur
 * d'une cote de mur, donc ce qu'on veut voir d'un coup.
 */
const TAILLE_DE_LA_LOUPE = 0.05

/**
 * Ce qu'un pixel de la LOUPE vaut en points PostScript de la page.
 *
 * La tuile fait 512 pixels pour une zone de `TAILLE_DE_LA_LOUPE` de page. Sur
 * une page de 3 370 points de large, cela donne 168 points rendus sur 512
 * pixels, soit 0,33 point par pixel.
 *
 * **C'est ce nombre qui part au serveur comme `resolution_du_pointage`**, et
 * c'est lui qui décide de l'incertitude de toutes les mesures. Le calculer
 * plutôt que de l'affirmer est ce qui rend l'incertitude honnête.
 *
 * **Le PLUS GRAND côté, et non la largeur.** Le défaut corrigé : cette
 * fonction ne prenait que la largeur, alors que `rendu_pdf.tuile` ajuste son
 * facteur sur le plus grand côté de la zone demandée. Les deux coïncident tant
 * que la page est en paysage — les quatre plans du propriétaire le sont tous —
 * et divergent de 1,41 dès qu'elle est en portrait, **dans le mauvais sens** :
 * l'écran annonçait alors une mesure plus sûre qu'elle ne l'est. Éprouvé par
 * `test_le_portrait_n_annonce_pas_une_mesure_plus_sure_que_le_paysage`.
 */
function resolutionDeLaLoupe(largeurEnPoints: number, hauteurEnPoints: number): number {
  const pointsRendus = Math.max(largeurEnPoints, hauteurEnPoints) * TAILLE_DE_LA_LOUPE
  return pointsRendus / 512
}

type Outil = 'naviguer' | 'calibrer' | 'segment' | 'surface'

/**
 * Le tracé à dessiner : fermé pour une surface, ouvert pour une longueur.
 *
 * Écrit une fois et appelé des deux côtés — l'aperçu et la loupe — parce que
 * deux fermetures écrites séparément finiraient par diverger, et qu'un contour
 * fermé d'un côté et ouvert de l'autre montrerait deux formes pour une seule
 * mesure.
 */
function fermerSiSurface(points: PointDEcran[], type: string): PointDEcran[] {
  const premier = points[0]
  if (type !== 'surface' || premier === undefined) return points
  return [...points, premier]
}

const OUTILS: { cle: Outil; libelle: string; aide: string }[] = [
  { cle: 'naviguer', libelle: 'Naviguer', aide: 'Cliquez pour ouvrir la loupe.' },
  {
    cle: 'calibrer',
    libelle: "Déclarer l'échelle",
    aide: 'Dans la loupe, cliquez les deux extrémités d’une cote connue.',
  },
  {
    cle: 'segment',
    libelle: 'Mesurer une longueur',
    aide: 'Dans la loupe, cliquez les points du tracé.',
  },
  {
    cle: 'surface',
    libelle: 'Mesurer une surface',
    aide: 'Dans la loupe, cliquez au moins trois points. Le contour se ferme seul.',
  },
]

/** Charge une image authentifiée et rend son URL d'objet. */
function useImageAuthentifiee(
  charger: () => Promise<Blob>,
  actif: boolean,
  cle: string,
): { url: string | null; enCours: boolean; erreur: unknown } {
  const [url, setUrl] = useState<string | null>(null)
  const [enCours, setEnCours] = useState(false)
  const [erreur, setErreur] = useState<unknown>(null)

  useEffect(() => {
    if (!actif) {
      setUrl(null)
      return
    }
    let abandonne = false
    let objet: string | null = null
    setEnCours(true)
    setErreur(null)
    charger()
      .then((blob) => {
        if (abandonne) return
        objet = URL.createObjectURL(blob)
        setUrl(objet)
      })
      .catch((cause) => {
        if (!abandonne) setErreur(cause)
      })
      .finally(() => {
        if (!abandonne) setEnCours(false)
      })
    return () => {
      abandonne = true
      // Une URL d'objet non révoquée retient son blob jusqu'au rechargement de
      // la page. Sur un plan de 1,2 Mo parcouru page à page, cela se voit.
      if (objet) URL.revokeObjectURL(objet)
    }
    // `charger` est recréée à chaque rendu : la clé porte ce qui change
    // vraiment, sans quoi l'image serait rechargée en boucle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actif, cle])

  return { url, enCours, erreur }
}

export function LecturePdf({
  documentId,
  revisionId,
  plan,
  peutValider,
  onRelire,
}: {
  documentId: string
  revisionId: string
  plan: PlanLu
  /**
   * Le porteur du jeton peut-il TRANCHER sur une mesure ?
   *
   * Reçu de `LecturePlan`, comme l'écran DXF le recevait déjà. Il ne l'était
   * pas ici, et un compte `buyer` ou `viewer` voyait donc trois commandes que
   * le serveur refusait ensuite par un 403. Le serveur reste l'autorité ; ce
   * drapeau évite de proposer un geste qui ne peut pas aboutir.
   */
  peutValider: boolean
  onRelire: () => void
}) {
  const [page, setPage] = useState(1)
  const [outil, setOutil] = useState<Outil>('naviguer')
  const [loupe, setLoupe] = useState<[number, number, number, number] | null>(null)
  const [points, setPoints] = useState<PointDEcran[]>([])
  const [textes, setTextes] = useState<TextesDePlan | null>(null)
  const [travail, setTravail] = useState<MesuresDePdf | null>(null)
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)
  // La mesure dont on veut VÉRIFIER le tracé. Elle n'est pas « la dernière » :
  // on sélectionne aussi une mesure ancienne pour la relire, et c'est le seul
  // moyen de contrôler ce qui a été pointé.
  const [mesureVisee, setMesureVisee] = useState<string | null>(null)

  const apercuDisponible = plan.apercus.includes(page)

  const apercu = useImageAuthentifiee(
    () => api.renduDeLaPage(documentId, revisionId, page),
    apercuDisponible,
    `apercu-${revisionId}-${page}`,
  )
  const tuile = useImageAuthentifiee(
    () => api.tuileDuPlan(documentId, revisionId, page, loupe!),
    loupe !== null,
    `tuile-${revisionId}-${page}-${loupe?.join(',') ?? ''}`,
  )

  useEffect(() => {
    api
      .textesDuPlan(documentId, revisionId, page)
      .then(setTextes)
      .catch(() => setTextes(null))
  }, [documentId, revisionId, page])

  const rechargerLeTravail = useCallback(() => {
    api
      .mesuresDuPdf(documentId, revisionId)
      .then(setTravail)
      .catch((cause) => setErreur(cause))
  }, [documentId, revisionId])

  useEffect(rechargerLeTravail, [rechargerLeTravail])

  /** Les deux dimensions de la page courante, en points PostScript. */
  const largeurDeLaPage = plan.dimensions_des_pages[page - 1]?.[0] ?? 0
  const hauteurDeLaPage = plan.dimensions_des_pages[page - 1]?.[1] ?? 0

  // Changer d'outil ou de page abandonne le tracé en cours : garder des points
  // désignés à un autre endroit produirait une mesure que personne n'a voulue.
  useEffect(() => setPoints([]), [outil, page])

  const calibrationDeLaPage = useMemo(
    () => travail?.calibrations.find((c) => c.page === page) ?? null,
    [travail, page],
  )
  const mesuresDeLaPage = useMemo(
    () => (travail?.mesures ?? []).filter((m) => m.page === page),
    [travail, page],
  )
  const toutesLesMesures = travail?.mesures ?? []
  const tranchees = toutesLesMesures.filter((m) => m.decision !== null).length
  const mesureSelectionnee = useMemo(
    () => toutesLesMesures.find((m) => m.proposal_id === mesureVisee) ?? null,
    [toutesLesMesures, mesureVisee],
  )

  function cliquerSurLApercu(evenement: React.MouseEvent<HTMLDivElement>) {
    const boite = evenement.currentTarget.getBoundingClientRect()
    const x = (evenement.clientX - boite.left) / boite.width
    const y = (evenement.clientY - boite.top) / boite.height
    const demi = TAILLE_DE_LA_LOUPE / 2
    setLoupe([
      Math.max(0, x - demi),
      Math.max(0, y - demi),
      Math.min(1, x + demi),
      Math.min(1, y + demi),
    ])
  }

  function cliquerDansLaLoupe(evenement: React.MouseEvent<HTMLDivElement>) {
    if (!loupe || outil === 'naviguer') return
    const boite = evenement.currentTarget.getBoundingClientRect()
    const dansLaLoupeX = (evenement.clientX - boite.left) / boite.width
    const dansLaLoupeY = (evenement.clientY - boite.top) / boite.height
    // De la loupe vers la page : la loupe est une fenêtre sur [0,1]².
    const point = {
      x: loupe[0] + dansLaLoupeX * (loupe[2] - loupe[0]),
      y: loupe[1] + dansLaLoupeY * (loupe[3] - loupe[1]),
    }
    const maximum = outil === 'calibrer' ? 2 : 200
    setPoints((anciens) => (anciens.length >= maximum ? [point] : [...anciens, point]))
  }

  return (
    <section className="card pdf-ecran" data-testid="pdf-lecture">
      <h2>{t('plan.pdf.titre')}</h2>
      <ErrorNotice error={erreur} />

      <EnTetePdf plan={plan} calibration={calibrationDeLaPage} />

      {/*
        Le fil d'état. Il existe parce que l'écran ne disait NULLE PART où l'on
        en était : il montrait des outils, un badge « aucune échelle », et
        laissait deviner que l'un conditionnait l'autre. Quatre étapes, celle
        en cours mise en avant, et chacune dit ce qu'elle attend.
      */}
      <FilDEtat
        calibration={calibrationDeLaPage}
        mesures={toutesLesMesures.length}
        tranchees={tranchees}
      />

      {/*
        **L'espace de mesure.** Le plan à gauche, ce sur quoi on agit à droite,
        et la liste en dessous.

        Le défaut corrigé : l'aperçu, puis une très grande loupe, puis les
        formulaires et les résultats bien plus bas. Pour nommer une mesure il
        fallait quitter le dessin des yeux ; pour vérifier un tracé, remonter.
        La colonne de gauche est donc COLLANTE : le plan reste à l'écran
        pendant qu'on agit et pendant qu'on relit la liste.
      */}
      <div className="pdf-espace">
        <div className="pdf-colonne-plan">
          {plan.pages > 1 && (
            <nav className="pdf-pages" aria-label={t('plan.pdf.pages')} data-testid="pdf-pages">
              <button
                type="button"
                data-testid="pdf-page-precedente"
                disabled={page <= 1}
                onClick={() => {
                  setPage((n) => n - 1)
                  setLoupe(null)
                }}
              >
                ‹
              </button>
              <span data-testid="pdf-page-courante">
                {t('plan.pdf.pageSur')
                  .replace('{page}', String(page))
                  .replace('{total}', String(plan.pages))}
              </span>
              <button
                type="button"
                data-testid="pdf-page-suivante"
                disabled={page >= plan.pages}
                onClick={() => {
                  setPage((n) => n + 1)
                  setLoupe(null)
                }}
              >
                ›
              </button>
            </nav>
          )}

          <div className="pdf-outils" role="group" aria-label={t('plan.pdf.outils')}>
            {OUTILS.map((choix) => (
              <button
                key={choix.cle}
                type="button"
                data-testid={`pdf-outil-${choix.cle}`}
                className={outil === choix.cle ? 'primary' : undefined}
                aria-pressed={outil === choix.cle}
                onClick={() => setOutil(choix.cle)}
              >
                {choix.libelle}
              </button>
            ))}
          </div>
          <p className="muted">{OUTILS.find((o) => o.cle === outil)?.aide}</p>
          {/* Pourquoi on ne pointe pas sur l'aperçu, dit une fois et à sa place. */}
          <p className="muted" data-testid="pdf-comment-pointer">
            {t('plan.pdf.commentPointer')}
          </p>

          {!apercuDisponible && (
            <div className="notice warning" role="alert">
              {t('plan.pdf.sansApercu')}
            </div>
          )}

          {apercu.enCours && <p className="muted">{t('common.loading')}</p>}
          {apercu.url && (
            <div className="pdf-apercu" data-testid="pdf-apercu" onClick={cliquerSurLApercu}>
              {/* Chargé comme IMAGE, jamais en ligne : un PNG est inerte, et le
                  rester explicitement vaut mieux que le rester par hasard. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={apercu.url}
                alt={t('plan.pdf.apercuAlt')}
                data-testid="pdf-apercu-image"
              />
              <Surlignages
                fragments={textes?.fragments ?? []}
                mesures={mesuresDeLaPage}
                selectionnee={mesureSelectionnee}
                loupe={loupe}
              />
            </div>
          )}
        </div>

        <div className="pdf-colonne-action">
          {loupe && (
            <Loupe
              url={tuile.url}
              enCours={tuile.enCours}
              erreur={tuile.erreur}
              points={points}
              selectionnee={mesureSelectionnee}
              loupe={loupe}
              outil={outil}
              onCliquer={cliquerDansLaLoupe}
              onFermer={() => {
                setLoupe(null)
                setPoints([])
              }}
            />
          )}

          {outil === 'calibrer' && points.length === 2 && (
            <FormulaireDeCalibration
              occupe={occupe}
              onValider={async (distance, unite, motif) => {
                setOccupe(true)
                setErreur(null)
                try {
                  // Destructuré avec une garde explicite : TypeScript ne déduit
                  // pas de `points.length === 2` que les deux cases existent, et
                  // il a raison — un tableau peut être modifié entre les deux.
                  const [premier, second] = points
                  if (!premier || !second) return
                  await api.calibrerLePlan(documentId, revisionId, {
                    page,
                    premier,
                    second,
                    distance_reelle: distance,
                    unite,
                    resolution_du_pointage: String(
                      resolutionDeLaLoupe(largeurDeLaPage, hauteurDeLaPage),
                    ),
                    motif,
                  })
                  setPoints([])
                  setOutil('naviguer')
                  rechargerLeTravail()
                } catch (cause) {
                  setErreur(cause)
                } finally {
                  setOccupe(false)
                }
              }}
            />
          )}

          {(outil === 'segment' || outil === 'surface') &&
            points.length >= (outil === 'surface' ? 3 : 2) && (
              <FormulaireDeMesure
                type={outil}
                nombreDePoints={points.length}
                occupe={occupe}
                sansCalibration={calibrationDeLaPage === null}
                onValider={async (libelle) => {
                  setOccupe(true)
                  setErreur(null)
                  try {
                    const creee = await api.mesurerSurLePdf(documentId, revisionId, {
                      page,
                      type: outil,
                      points,
                      libelle,
                    })
                    setPoints([])
                    // La mesure qu'on vient de créer devient celle qu'on
                    // regarde : son tracé s'affiche sans qu'il faille la
                    // chercher dans la liste pour vérifier ce qu'on a pointé.
                    setMesureVisee(creee.proposal_id)
                    rechargerLeTravail()
                    onRelire()
                  } catch (cause) {
                    setErreur(cause)
                  } finally {
                    setOccupe(false)
                  }
                }}
              />
            )}

          {/* Ce qu'on est en train de vérifier, à côté du dessin et non sous
              la liste : une valeur qu'il faut aller chercher trois écrans
              plus bas ne se vérifie pas. */}
          {mesureSelectionnee && (
            <MesureRegardee
              mesure={mesureSelectionnee}
              onFermer={() => setMesureVisee(null)}
            />
          )}
        </div>
      </div>

      <ListeDesMesures
        mesures={travail?.mesures ?? []}
        selectionnee={mesureVisee}
        peutValider={peutValider}
        onDecidee={() => {
          rechargerLeTravail()
          onRelire()
        }}
        onMontrer={(mesure) => {
          setPage(mesure.page)
          setMesureVisee(mesure.proposal_id)
          if (mesure.cadre) {
            const demi = TAILLE_DE_LA_LOUPE / 2
            const cx = (Number(mesure.cadre.x0) + Number(mesure.cadre.x1)) / 2
            const cy = (Number(mesure.cadre.y0) + Number(mesure.cadre.y1)) / 2
            setLoupe([
              Math.max(0, cx - demi),
              Math.max(0, cy - demi),
              Math.min(1, cx + demi),
              Math.min(1, cy + demi),
            ])
          }
        }}
      />

      {textes && (
        <p className="muted" data-testid="pdf-textes-lus">
          {t('plan.pdf.textesLus')
            .replace('{rendus}', String(textes.fragments.length))
            .replace('{total}', String(textes.total))}
        </p>
      )}
    </section>
  )
}

/**
 * Où l'on en est, en quatre étapes.
 *
 * Elles ne sont pas décoratives : ce sont les quatre états réels du parcours,
 * et chacune est déduite de la BASE — une échelle existe ou non, des mesures
 * existent ou non, elles sont tranchées ou non. Un fil qui avancerait sur un
 * compteur local mentirait dès qu'on recharge la page.
 */
function FilDEtat({
  calibration,
  mesures,
  tranchees,
}: {
  calibration: CalibrationDePlan | null
  mesures: number
  tranchees: number
}) {
  const etapes = [
    {
      cle: 'echelle',
      titre: calibration ? t('plan.pdf.etape.echelleFaite') : t('plan.pdf.etape.echelle'),
      aide: t('plan.pdf.etape.echelleAide'),
      faite: calibration !== null,
      enCours: calibration === null,
    },
    {
      cle: 'mesurer',
      titre:
        mesures > 0
          ? t('plan.pdf.etape.mesurerFaite').replace('{nombre}', String(mesures))
          : t('plan.pdf.etape.mesurer'),
      aide: t('plan.pdf.etape.mesurerAide'),
      faite: mesures > 0,
      enCours: calibration !== null && mesures === 0,
    },
    {
      cle: 'decider',
      titre:
        mesures > 0
          ? t('plan.pdf.etape.deciderFaite')
              .replace('{tranchees}', String(tranchees))
              .replace('{total}', String(mesures))
          : t('plan.pdf.etape.decider'),
      aide: t('plan.pdf.etape.deciderAide'),
      faite: mesures > 0 && tranchees === mesures,
      enCours: mesures > 0 && tranchees < mesures,
    },
  ]

  return (
    <ol className="pdf-fil" data-testid="pdf-fil" aria-label={t('plan.pdf.fil')}>
      {etapes.map((etape) => (
        <li
          key={etape.cle}
          data-testid={`pdf-etape-${etape.cle}`}
          data-etat={etape.enCours ? 'en-cours' : etape.faite ? 'faite' : 'a-faire'}
          aria-current={etape.enCours ? 'step' : undefined}
        >
          <strong>{etape.titre}</strong>
          <span className="muted">
            {etape.enCours
              ? etape.aide
              : etape.faite
                ? t('plan.pdf.etape.faite')
                : t('plan.pdf.etape.aFaire')}
          </span>
        </li>
      ))}
    </ol>
  )
}

/**
 * La mesure qu'on est en train de vérifier, posée à côté du dessin.
 *
 * Elle répète ce que la ligne du tableau porte déjà, et ce n'est pas une
 * redondance : le tableau est en bas, le dessin en haut, et vérifier un tracé
 * demande d'avoir les deux sous les yeux. C'est exactement ce que l'ancienne
 * disposition rendait impossible.
 */
function MesureRegardee({
  mesure,
  onFermer,
}: {
  mesure: MesureDePdf
  onFermer: () => void
}) {
  return (
    <div className="pdf-regardee" data-testid="pdf-mesure-regardee">
      <div className="pdf-loupe-entete">
        <strong>{mesure.libelle}</strong>
        <button type="button" data-testid="pdf-regardee-fermer" onClick={onFermer}>
          {t('common.close')}
        </button>
      </div>
      <p className="muted">{t('plan.pdf.traceAffiche')}</p>
      <dl className="pdf-entete">
        <div>
          <dt>{t('plan.pdf.colMesuree')}</dt>
          <dd>
            <strong data-testid="pdf-regardee-valeur">{mesure.valeur_lisible}</strong>
            <br />
            <span className="muted">{mesure.incertitude_lisible}</span>
          </dd>
        </div>
        <div>
          <dt>{t('plan.pdf.colRetenue')}</dt>
          <dd data-testid="pdf-regardee-retenue">
            {mesure.valeur_retenue_lisible ?? (
              <span className="muted">
                {mesure.decision === 'rejected'
                  ? t('plan.pdf.rienARetenir')
                  : t('plan.pdf.pasEncoreTranchee')}
              </span>
            )}
          </dd>
        </div>
      </dl>
    </div>
  )
}

function EnTetePdf({
  plan,
  calibration,
}: {
  plan: PlanLu
  calibration: CalibrationDePlan | null
}) {
  return (
    <dl className="pdf-entete" data-testid="pdf-entete">
      <div>
        <dt>{t('plan.pdf.pagesLabel')}</dt>
        <dd>{plan.pages}</dd>
      </div>
      <div>
        <dt>{t('plan.pdf.texteLabel')}</dt>
        {/*
          Le COMPTE d'abord, la réserve ensuite. L'en-tête affichait « aucun »
          dès que le seuil d'extraction n'était pas franchi, pendant que le bas
          de l'écran annonçait « 4 textes affichés sur 4 lus ». Deux phrases
          contradictoires sur la même page : l'une parlait du seuil, l'autre du
          nombre lu, et rien ne le disait.
        */}
        <dd data-testid="pdf-textes-entete">
          {plan.fragments_lus > 0 ? plan.fragments_lus : t('plan.pdf.sansTexte')}
          {plan.fragments_lus > 0 && !plan.porte_du_texte && (
            <>
              <br />
              <span className="muted">{t('plan.pdf.texteMaigre')}</span>
            </>
          )}
        </dd>
      </div>
      <div>
        <dt>{t('plan.pdf.echelleLabel')}</dt>
        {/* Le point le plus important de cet en-tête : sans échelle confirmée,
            aucune mesure n'est possible, et le dire d'emblée évite de laisser
            quelqu'un chercher pourquoi le bouton « mesurer » ne donne rien. */}
        <dd data-testid="pdf-echelle">
          {calibration ? (
            <>
              <strong data-testid="pdf-facteur">{calibration.facteur_lisible}</strong>
              <br />
              <span className="muted">{calibration.motif}</span>
            </>
          ) : (
            <span className="badge warning">{t('plan.pdf.sansEchelle')}</span>
          )}
        </dd>
      </div>
    </dl>
  )
}

/** Les boîtes posées sur l'aperçu : textes lus, mesures prises, loupe ouverte. */
function Surlignages({
  fragments,
  mesures,
  selectionnee,
  loupe,
}: {
  fragments: FragmentDeTexte[]
  mesures: MesureDePdf[]
  /** Celle qu'on vérifie : tracée en évidence, et ses sommets marqués. */
  selectionnee: MesureDePdf | null
  loupe: [number, number, number, number] | null
}) {
  return (
    <svg className="pdf-surlignages" viewBox="0 0 1 1" preserveAspectRatio="none">
      {fragments
        .filter((fragment) => fragment.cadre !== null)
        .map((fragment, rang) => {
          const c = fragment.cadre!
          return (
            <rect
              key={`t${rang}`}
              className="pdf-texte"
              data-testid="pdf-texte"
              data-texte={fragment.texte}
              x={Number(c.x0)}
              y={Number(c.y0)}
              width={Number(c.x1) - Number(c.x0)}
              height={Number(c.y1) - Number(c.y0)}
            >
              {/*
                Le texte lu, porté par le surlignage lui-même. À 2,4 pixels de
                haut, le propriétaire voit QU'IL Y A une information sans
                pouvoir la lire : ce `<title>` la lui donne au survol, et la
                donne aussi aux lecteurs d'écran, pour qui un rectangle sans
                nom n'existe pas.
              */}
              <title>{fragment.texte}</title>
            </rect>
          )
        })}
      {mesures.map((mesure) => (
        <polyline
          key={mesure.proposal_id}
          data-testid="pdf-trace-mesure"
          className={
            (mesure.fiabilite === 'mesurable' ? 'pdf-mesure' : 'pdf-mesure douteuse') +
            (mesure.proposal_id === selectionnee?.proposal_id ? ' selectionnee' : '')
          }
          points={fermerSiSurface(mesure.points, mesure.type)
            .map((p) => `${p.x},${p.y}`)
            .join(' ')}
        />
      ))}
      {/*
        Les SOMMETS de la mesure vérifiée, et eux seuls.

        « Afficher son tracé » ne suffit pas à vérifier ce qui a été pointé :
        sur une ligne, deux extrémités mal posées donnent le même trait qu'une
        bonne, décalé. Les marquer est ce qui permet de dire « ce point-là
        n'est pas sur l'angle du mur ». Les poser pour TOUTES les mesures
        couvrirait le dessin de pastilles.
      */}
      {selectionnee?.points.map((point, rang) => (
        <circle
          key={`s${rang}`}
          className="pdf-sommet"
          data-testid="pdf-sommet-mesure"
          cx={point.x}
          cy={point.y}
          r={0.006}
        />
      ))}
      {loupe && (
        <rect
          className="pdf-loupe"
          x={loupe[0]}
          y={loupe[1]}
          width={loupe[2] - loupe[0]}
          height={loupe[3] - loupe[1]}
        />
      )}
    </svg>
  )
}

function Loupe({
  url,
  enCours,
  erreur,
  points,
  selectionnee,
  loupe,
  outil,
  onCliquer,
  onFermer,
}: {
  url: string | null
  enCours: boolean
  erreur: unknown
  points: PointDEcran[]
  /** La mesure qu'on vérifie, redessinée DANS la loupe : c'est là qu'on voit
      si un sommet est posé sur l'angle du mur ou à trois pixels de lui. */
  selectionnee: MesureDePdf | null
  loupe: [number, number, number, number]
  outil: Outil
  onCliquer: (evenement: React.MouseEvent<HTMLDivElement>) => void
  onFermer: () => void
}) {
  return (
    <div className="pdf-loupe-panneau" data-testid="pdf-loupe-panneau">
      <div className="pdf-loupe-entete">
        <strong>{t('plan.pdf.loupe')}</strong>
        <button type="button" data-testid="pdf-loupe-fermer" onClick={onFermer}>
          {t('common.close')}
        </button>
      </div>
      {/* L'attente est ANNONCÉE, parce qu'elle est longue et qu'elle a une
          cause : charger une page de PDF coûte jusqu'à 5,3 secondes la
          première fois. Les suivantes viennent du cache, en 0,3 ms. */}
      {enCours && <p className="muted">{t('plan.pdf.loupeEnCours')}</p>}
      <ErrorNotice error={erreur} />
      {url && (
        <div className="pdf-loupe-image" data-testid="pdf-loupe-image" onClick={onCliquer}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={url} alt={t('plan.pdf.loupeAlt')} data-testid="pdf-loupe-rendu" />
          <svg className="pdf-surlignages" viewBox="0 0 1 1" preserveAspectRatio="none">
            {selectionnee && (
              <polyline
                className="pdf-mesure selectionnee"
                data-testid="pdf-loupe-trace-mesure"
                points={fermerSiSurface(selectionnee.points, selectionnee.type)
                  .map(
                    (p) =>
                      `${(p.x - loupe[0]) / (loupe[2] - loupe[0])},` +
                      `${(p.y - loupe[1]) / (loupe[3] - loupe[1])}`,
                  )
                  .join(' ')}
              />
            )}
            {selectionnee?.points.map((point, rang) => (
              <circle
                key={`sl${rang}`}
                className="pdf-sommet"
                data-testid="pdf-loupe-sommet"
                cx={(point.x - loupe[0]) / (loupe[2] - loupe[0])}
                cy={(point.y - loupe[1]) / (loupe[3] - loupe[1])}
                r={0.014}
              />
            ))}
            {points.map((point, rang) => (
              <circle
                key={rang}
                className="pdf-point"
                cx={(point.x - loupe[0]) / (loupe[2] - loupe[0])}
                cy={(point.y - loupe[1]) / (loupe[3] - loupe[1])}
                r={0.012}
              />
            ))}
            {points.length >= 2 && (
              <polyline
                className="pdf-trace"
                points={fermerSiSurface(points, outil)
                  .map(
                    (p) =>
                      `${(p.x - loupe[0]) / (loupe[2] - loupe[0])},` +
                      `${(p.y - loupe[1]) / (loupe[3] - loupe[1])}`,
                  )
                  .join(' ')}
              />
            )}
          </svg>
        </div>
      )}
      {outil !== 'naviguer' && (
        <>
          <p className="muted">
            {t('plan.pdf.pointsPoses').replace('{nombre}', String(points.length))}
          </p>
          {/* Le piège que personne ne devine : une cote plus longue que la
              loupe reste calibrable, à condition de savoir qu'on peut bouger
              la loupe entre les deux clics. */}
          <p className="muted" data-testid="pdf-loupe-deplacable">
            {t('plan.pdf.loupeDeplacable')}
          </p>
        </>
      )}
    </div>
  )
}

function FormulaireDeCalibration({
  occupe,
  onValider,
}: {
  occupe: boolean
  onValider: (distance: string, unite: string, motif: string) => void
}) {
  const [distance, setDistance] = useState('')
  const [unite, setUnite] = useState<string>('mm')
  const [motif, setMotif] = useState('')

  return (
    <form
      className="pdf-formulaire"
      data-testid="pdf-formulaire-calibration"
      onSubmit={(evenement) => {
        evenement.preventDefault()
        onValider(distance.trim(), unite, motif.trim())
      }}
    >
      <h3>{t('plan.pdf.confirmerEchelle')}</h3>
      {/* La phrase qui évite le contresens le plus coûteux : ce n'est pas une
          échelle au sens « 1:50 » qu'on saisit, c'est la distance RÉELLE entre
          les deux points qu'on vient de désigner. */}
      <p className="muted">{t('plan.pdf.aideEchelle')}</p>
      <label htmlFor="calib-distance">{t('plan.pdf.distanceReelle')}</label>
      <input
        id="calib-distance"
        value={distance}
        onChange={(e) => setDistance(e.target.value)}
        inputMode="decimal"
        required
      />
      <label htmlFor="calib-unite">{t('plan.pdf.unite')}</label>
      <select id="calib-unite" value={unite} onChange={(e) => setUnite(e.target.value)}>
        {UNITES.map((code) => (
          <option key={code} value={code}>
            {code}
          </option>
        ))}
      </select>
      <label htmlFor="calib-motif">{t('plan.pdf.motif')}</label>
      <input
        id="calib-motif"
        value={motif}
        onChange={(e) => setMotif(e.target.value)}
        placeholder={t('plan.pdf.motifExemple')}
        required
      />
      <button
        className="primary"
        type="submit"
        data-testid="pdf-calibrer"
        disabled={occupe}
      >
        {occupe ? t('common.saving') : t('plan.pdf.confirmer')}
      </button>
    </form>
  )
}

function FormulaireDeMesure({
  type,
  nombreDePoints,
  occupe,
  sansCalibration,
  onValider,
}: {
  type: 'segment' | 'surface'
  nombreDePoints: number
  occupe: boolean
  sansCalibration: boolean
  onValider: (libelle: string) => void
}) {
  const [libelle, setLibelle] = useState('')
  return (
    <form
      className="pdf-formulaire"
      data-testid="pdf-formulaire-mesure"
      onSubmit={(evenement) => {
        evenement.preventDefault()
        onValider(libelle.trim())
      }}
    >
      <h3>
        {type === 'segment' ? t('plan.pdf.nommerLongueur') : t('plan.pdf.nommerSurface')}
      </h3>
      {sansCalibration && (
        <div className="notice warning" role="alert">
          {t('plan.pdf.sansEchelleMesure')}
        </div>
      )}
      <p className="muted">
        {t('plan.pdf.pointsPoses').replace('{nombre}', String(nombreDePoints))}
      </p>
      <label htmlFor="mesure-libelle">{t('plan.pdf.libelle')}</label>
      <input
        id="mesure-libelle"
        value={libelle}
        onChange={(e) => setLibelle(e.target.value)}
        placeholder={t('plan.pdf.libelleExemple')}
        required
      />
      <button
        className="primary"
        type="submit"
        data-testid="pdf-mesurer"
        disabled={occupe || sansCalibration}
      >
        {occupe ? t('common.saving') : t('plan.pdf.mesurer')}
      </button>
    </form>
  )
}

function ListeDesMesures({
  mesures,
  selectionnee,
  peutValider,
  onDecidee,
  onMontrer,
}: {
  mesures: MesureDePdf[]
  selectionnee: string | null
  peutValider: boolean
  onDecidee: () => void
  onMontrer: (mesure: MesureDePdf) => void
}) {
  if (mesures.length === 0) {
    return <p className="muted">{t('plan.pdf.aucuneMesure')}</p>
  }
  return (
    <table className="pdf-mesures" data-testid="pdf-mesures">
      <caption>{t('plan.pdf.mesuresPrises')}</caption>
      <thead>
        <tr>
          <th scope="col">{t('plan.pdf.colLibelle')}</th>
          {/*
            DEUX colonnes de nombres, et c'est tout le propos : ce que Metreo a
            calculé, et ce que la personne retient. Les fondre en une seule —
            l'état d'avant — laissait croire qu'une proposition machine était
            déjà une quantité, et qu'une correction remplaçait la mesure. Ni
            l'un ni l'autre n'est vrai.
          */}
          <th scope="col">{t('plan.pdf.colMesuree')}</th>
          <th scope="col">{t('plan.pdf.colRetenue')}</th>
          <th scope="col">{t('plan.pdf.colFiabilite')}</th>
          <th scope="col">{t('plan.pdf.colDecision')}</th>
          <th scope="col">{t('plan.pdf.colActions')}</th>
        </tr>
      </thead>
      <tbody>
        {mesures.map((mesure) => (
          <LigneDeMesurePdf
            key={mesure.proposal_id}
            mesure={mesure}
            selectionnee={mesure.proposal_id === selectionnee}
            peutValider={peutValider}
            onDecidee={onDecidee}
            onMontrer={() => onMontrer(mesure)}
          />
        ))}
      </tbody>
    </table>
  )
}

/**
 * Une mesure, et la décision qu'une personne prend dessus.
 *
 * **Ce que cette ligne sépare, et qu'elle ne séparait pas.** À gauche, ce que
 * Metreo a CALCULÉ ; à droite, ce que la personne RETIENT. C'étaient deux
 * nombres dans la même case, l'un sous l'autre, et le second passait pour une
 * correction du premier alors qu'il le remplace sans l'effacer.
 *
 * **Et ce qu'elle dit désormais à voix haute** : si cette mesure peut
 * alimenter un bordereau. Une mesure rejetée ne le peut jamais, une mesure non
 * tranchée non plus — c'est la règle du produit, et elle était jusqu'ici une
 * phrase dans un guide plutôt qu'un mot à l'écran.
 */
function LigneDeMesurePdf({
  mesure,
  selectionnee,
  peutValider,
  onDecidee,
  onMontrer,
}: {
  mesure: MesureDePdf
  selectionnee: boolean
  peutValider: boolean
  onDecidee: () => void
  onMontrer: () => void
}) {
  const [correction, setCorrection] = useState('')
  const [motif, setMotif] = useState('')
  const [occupe, setOccupe] = useState(false)
  const [erreur, setErreur] = useState<unknown>(null)

  async function decider(decision: 'accepted' | 'corrected' | 'rejected') {
    setOccupe(true)
    setErreur(null)
    try {
      await api.deciderProposition(
        mesure.proposal_id,
        decision === 'corrected'
          ? {
              decision,
              reason: motif.trim(),
              // L'unité voyage AVEC le nombre, des deux côtés. Sans elle, le
              // serveur ne pourrait pas refuser une surface corrigée en
              // millimètres, et le journal ne dirait pas en quoi la personne
              // a répondu.
              before_value: { valeur: mesure.valeur, unite: mesure.unite },
              after_value: { valeur: correction.trim(), unite: mesure.unite },
            }
          : { decision, reason: motif.trim() },
      )
      setCorrection('')
      setMotif('')
      onDecidee()
    } catch (cause) {
      setErreur(cause)
    } finally {
      setOccupe(false)
    }
  }

  return (
    <tr data-testid="pdf-mesure" data-selectionnee={selectionnee ? 'oui' : undefined}>
      <td data-testid="pdf-libelle">
        {mesure.libelle}
        <br />
        <span className="muted">
          {t('plan.pdf.pageCourte').replace('{page}', String(mesure.page))} ·{' '}
          {mesure.type === 'segment' ? t('plan.pdf.longueur') : t('plan.pdf.surface')}
        </span>
      </td>

      {/* Ce que Metreo a calculé. Jamais réécrit, quoi qu'il arrive ensuite. */}
      <td>
        <strong data-testid="pdf-valeur">{mesure.valeur_lisible}</strong>
        <br />
        {/* L'incertitude, dans la MÊME unité que la valeur et arrondie au même
            rang : « 4 181 mm ± 26 mm ». En pourcentage, elle demanderait une
            multiplication que personne ne fait ; à dix décimales, elle
            annoncerait une précision que l'on vient de déclarer absente. */}
        <span className="muted" data-testid="pdf-incertitude">
          {mesure.incertitude_lisible}
        </span>
        <br />
        <span className="muted">{t('plan.pdf.calculeePar')}</span>
      </td>

      {/* Ce que la personne retient. Vide tant qu'elle n'a pas tranché. */}
      <td data-testid="pdf-valeur-retenue">
        {mesure.valeur_retenue_lisible ? (
          <strong className="badge">{mesure.valeur_retenue_lisible}</strong>
        ) : (
          <span className="muted">
            {mesure.decision === 'rejected'
              ? t('plan.pdf.rienARetenir')
              : t('plan.pdf.pasEncoreTranchee')}
          </span>
        )}
        <br />
        <span
          className={mesure.reprenable ? 'badge' : 'muted'}
          data-testid="pdf-reprenable"
          data-reprenable={mesure.reprenable ? 'oui' : 'non'}
        >
          {mesure.reprenable ? t('plan.pdf.reprenable') : t('plan.pdf.nonReprenable')}
        </span>
      </td>

      <td>
        <span
          data-testid="pdf-fiabilite"
          className={mesure.fiabilite === 'mesurable' ? 'badge' : 'badge warning'}
        >
          {mesure.fiabilite === 'mesurable'
            ? t('plan.pdf.mesurable')
            : t('plan.pdf.aVerifier')}
        </span>
        {mesure.reserves.map((reserve) => (
          <div key={reserve} className="muted">
            {t(`plan.pdf.reserve.${reserve}`)}
          </div>
        ))}
        <div className="muted">
          {t('plan.pdf.depuis').replace('{motif}', String(mesure.calibration.motif ?? ''))}
        </div>
      </td>

      <td data-testid="pdf-decision">
        {mesure.decision ? t(`plan.decision.${mesure.decision}`) : '—'}
        {/* Le motif voyage avec la décision : dans six mois, « 3,80 m » ne
            vaut que si l'on sait d'où ce nombre vient. */}
        {mesure.motif_de_la_decision && (
          <div className="muted" data-testid="pdf-motif-retenu">
            {t('plan.pdf.motifRetenu').replace('{motif}', mesure.motif_de_la_decision)}
          </div>
        )}
      </td>

      <td>
        <ErrorNotice error={erreur} />
        <button type="button" data-testid="pdf-montrer" onClick={onMontrer}>
          {selectionnee ? t('plan.pdf.traceAffiche') : t('plan.pdf.voirLeTrace')}
        </button>
        {/*
          Les trois commandes de décision sont MASQUÉES — et non grisées — pour
          qui n'a pas le droit de trancher. Le serveur refuse de toute façon
          par un 403 ; proposer un geste qui ne peut jamais aboutir n'apprend
          rien d'actionnable. L'écran DXF le faisait déjà ; celui-ci ne le
          faisait pas, et son propre commentaire d'en face affirmait le
          contraire.
        */}
        {peutValider ? (
          <>
            <input
              aria-label={t('plan.pdf.motifDecision')}
              data-testid="pdf-motif-decision"
              value={motif}
              onChange={(e) => setMotif(e.target.value)}
              placeholder={t('plan.pdf.motifDecision')}
            />
            <button
              type="button"
              data-testid="pdf-confirmer-mesure"
              disabled={occupe || !motif.trim()}
              onClick={() => decider('accepted')}
            >
              {t('plan.pdf.confirmerMesure')}
            </button>
            <span className="pdf-correction">
              <input
                aria-label={t('plan.pdf.valeurCorrigee')}
                data-testid="pdf-correction"
                value={correction}
                onChange={(e) => setCorrection(e.target.value)}
                placeholder={t('plan.pdf.valeurCorrigee')}
                inputMode="decimal"
              />
              {/* L'unité, affichée et NON saisissable : on corrige un nombre,
                  pas une unité. Corriger une surface en millimètres
                  mélangerait deux dimensions, et le serveur le refuse — autant
                  ne pas le proposer. */}
              <span className="muted" data-testid="pdf-unite-correction">
                {mesure.valeur_lisible.split(' ').slice(-1)[0]}
              </span>
            </span>
            <button
              type="button"
              data-testid="pdf-corriger"
              disabled={occupe || !motif.trim() || !correction.trim()}
              onClick={() => decider('corrected')}
            >
              {t('plan.pdf.corriger')}
            </button>
            {/*
              Le rejet manquait, et son absence était un piège. `decider`
              l'accepte depuis le début, la clé `plan.decision.rejected`
              existe, et la colonne « Décision » sait l'afficher : l'écran
              annonçait donc un vocabulaire qu'aucun bouton ne pouvait
              produire. Sans lui, une mesure visiblement fausse ne laissait que
              deux issues — la confirmer, ou la « corriger » vers une valeur
              que la personne ne connaît pas.

              Comme les deux autres, il exige un motif : une décision sans
              raison n'est pas une décision, et c'est elle qui sera relue dans
              six mois.
            */}
            <button
              type="button"
              data-testid="pdf-rejeter"
              disabled={occupe || !motif.trim()}
              onClick={() => decider('rejected')}
              title={t('plan.pdf.aideRejeter')}
            >
              {t('plan.pdf.rejeter')}
            </button>
          </>
        ) : (
          // Commandes masquées, phrase conservée : l'absence doit s'expliquer.
          <p className="muted" data-testid="pdf-sans-droit-de-valider">
            {t('plan.decide.notAllowed')}
          </p>
        )}
      </td>
    </tr>
  )
}
