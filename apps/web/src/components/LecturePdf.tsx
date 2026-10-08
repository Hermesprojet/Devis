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

import Link from 'next/link'

import {
  api,
  type ApercuDeReprise,
  type Boq,
  type BoqItem,
  type CalibrationDePlan,
  type FragmentDeTexte,
  type MesureDePdf,
  type MesuresDePdf,
  type PlanLu,
  type PointDEcran,
  type TextesDePlan,
  type UniteConnue,
} from '@/lib/api'
import { t } from '@/lib/i18n'
import { uniteLisible } from '../lib/unites'
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
 * Ce qu'un pixel AFFICHÉ vaut en points PostScript de la page.
 *
 * **C'est ce nombre qui part au serveur comme `resolution_du_pointage`**, et
 * c'est lui qui décide de l'incertitude de toutes les mesures. Le MESURER
 * plutôt que le supposer est ce qui rend l'incertitude honnête.
 *
 * **Il était supposé, et de trois façons fausses à la fois.** L'ancienne
 * version calculait `largeur_de_page × 0,05 ÷ 512` :
 *
 * 1. `512` est la taille que le serveur VISE, pas celle qu'il produit : un
 *    bitmap se compte en pixels entiers, et la loupe mesurée fait 511 × 389 ;
 * 2. l'image n'est pas affichée à sa taille naturelle. Elle remplit la largeur
 *    de sa colonne — 417,6 pixels CSS pour 511 pixels de bitmap dans la
 *    disposition à deux colonnes — et c'est le pixel AFFICHÉ que l'utilisateur
 *    vise, pas celui du fichier ;
 * 3. la largeur de page ignorait l'orientation, ce qui donnait 1,41 d'écart en
 *    portrait, dans le sens qui annonce une mesure plus sûre qu'elle ne l'est.
 *
 * Les trois disparaissent en prenant la zone RÉELLEMENT couverte et la taille
 * RÉELLEMENT affichée. Rien n'est supposé : les deux se lisent.
 *
 * Le plus grand des deux axes est retenu. Ils coïncident tant que l'image n'est
 * pas déformée ; s'ils divergent, annoncer le plus fin reviendrait à promettre
 * une précision que l'autre axe ne tient pas.
 */
function resolutionAffichee(
  image: HTMLImageElement,
  zone: [number, number, number, number],
  largeurDeLaPage: number,
  hauteurDeLaPage: number,
): number | null {
  const boite = image.getBoundingClientRect()
  if (boite.width <= 0 || boite.height <= 0) return null
  const pointsEnLargeur = (zone[2] - zone[0]) * largeurDeLaPage
  const pointsEnHauteur = (zone[3] - zone[1]) * hauteurDeLaPage
  return Math.max(pointsEnLargeur / boite.width, pointsEnHauteur / boite.height)
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

/**
 * Ce qu'une image affichée EST, et non seulement qu'elle est là.
 *
 * **Le défaut que ce type ferme.** L'ancien crochet ne rendait qu'une URL. Quand
 * on déplaçait la loupe, la zone changeait immédiatement, l'ANCIENNE image
 * restait affichée pendant le chargement de la nouvelle, et le clic restait
 * autorisé. Un point posé à cet instant était calculé sur la zone COURANTE
 * alors que l'utilisateur visait ce que montrait l'image PRÉCÉDENTE : un point
 * enregistré ailleurs que là où il a été vu, sans un mot.
 *
 * Chaque rendu porte donc son identité — révision, page, zone demandée — et
 * l'écran n'autorise le pointage que lorsque l'identité de l'image affichée est
 * celle de la zone courante.
 */
type RenduAffiche = {
  url: string
  /** L'identité de CE rendu : `revision|page|zone`. */
  cle: string
  /** La zone que l'image couvre vraiment, rendue par le serveur. */
  zone: [number, number, number, number]
  /** Faux quand le serveur n'a pas dit sa zone et qu'on retombe sur la demande. */
  zoneDeclaree: boolean
}

/**
 * Charge une image authentifiée et rend ce qui est RÉELLEMENT affiché.
 *
 * Trois propriétés, et chacune corrige un cas observé :
 *
 * - **une réponse en retard ne remplace jamais une plus récente.** Deux
 *   déplacements rapides lancent deux requêtes ; si la première arrive après
 *   la seconde, l'écran afficherait la mauvaise zone en se croyant à jour. La
 *   clé en cours est comparée au retour ;
 * - **l'ancienne image disparaît dès que la zone change.** Elle restait visible
 *   pour éviter un clignotement, au prix d'un décalage silencieux entre ce
 *   qu'on voit et ce qu'on désigne. L'attente est annoncée, c'est plus honnête
 *   qu'un dessin périmé ;
 * - **un échec de chargement n'affiche rien.** Garder l'image précédente après
 *   une erreur laisserait pointer sur une zone qu'on ne sert plus.
 */
function useRenduAuthentifie(
  charger: () => Promise<{ blob: Blob; zone: [number, number, number, number]; zoneDeclaree: boolean }>,
  actif: boolean,
  cle: string,
): { rendu: RenduAffiche | null; enCours: boolean; erreur: unknown } {
  const [rendu, setRendu] = useState<RenduAffiche | null>(null)
  const [enCours, setEnCours] = useState(false)
  const [erreur, setErreur] = useState<unknown>(null)

  useEffect(() => {
    if (!actif) {
      setRendu(null)
      setErreur(null)
      return
    }
    let abandonne = false
    let objet: string | null = null
    // L'image précédente part AVANT la nouvelle : tant que celle-ci n'est pas
    // là, l'écran n'a rien à montrer et rien à laisser désigner.
    setRendu(null)
    setEnCours(true)
    setErreur(null)
    charger()
      .then((tuile) => {
        if (abandonne) return
        objet = URL.createObjectURL(tuile.blob)
        setRendu({ url: objet, cle, zone: tuile.zone, zoneDeclaree: tuile.zoneDeclaree })
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

  return { rendu, enCours, erreur }
}

/**
 * Où un clic est tombé DANS une image, en fraction de l'image elle-même.
 *
 * **Et non en fraction de son conteneur.** Mesuré dans un navigateur :
 * l'enveloppe de l'aperçu fait 668,39 pixels pour une image de 666,39, et celle
 * de la loupe 419,61 pour 417,61 — un pixel de bordure de chaque côté. Les
 * clics étaient rapportés à l'enveloppe, ce qui décalait chaque point d'un
 * pixel ET le mettait à une échelle de 0,3 % trop grande. Les deux erreurs sont
 * systématiques : elles entrent dans l'échelle déclarée, puis multiplient
 * toutes les mesures de la page.
 *
 * Rend `null` si le clic tombe hors de l'image — sur la bordure, précisément.
 */
function fractionDansLImage(
  evenement: React.MouseEvent<HTMLElement>,
): { fx: number; fy: number } | null {
  const image = evenement.currentTarget.querySelector('img')
  if (!image) return null
  const boite = image.getBoundingClientRect()
  if (boite.width <= 0 || boite.height <= 0) return null
  const fx = (evenement.clientX - boite.left) / boite.width
  const fy = (evenement.clientY - boite.top) / boite.height
  if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return null
  return { fx, fy }
}

export function LecturePdf({
  documentId,
  revisionId,
  projectId,
  plan,
  peutValider,
  peutReprendre,
  onRelire,
}: {
  documentId: string
  revisionId: string
  /**
   * Le chantier, et non seulement le document.
   *
   * Il est nécessaire depuis que l'écran propose de reprendre une mesure dans
   * un bordereau : les bordereaux appartiennent au PROJET, et l'API les sert
   * sous `/projects/{id}/boqs`. Le chemin de cette page le porte déjà.
   */
  projectId: string
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
  /**
   * Le porteur du jeton peut-il écrire dans un bordereau ?
   *
   * Distinct de `peutValider` : trancher sur une mesure est un geste de
   * lecture de plan (`DOCUMENT_VALIDATE`), remplir un bordereau en est un de
   * métré (`BOQ_WRITE`). Un métreur a les deux ; un valideur documentaire peut
   * n'avoir que le premier. Proposer une commande que le serveur refusera par
   * un 403 n'apprend rien à personne.
   */
  peutReprendre: boolean
  onRelire: () => void
}) {
  const [page, setPage] = useState(1)
  const [outil, setOutil] = useState<Outil>('naviguer')
  const [loupe, setLoupe] = useState<[number, number, number, number] | null>(null)
  const [points, setPoints] = useState<PointDEcran[]>([])
  // La résolution la plus GROSSIÈRE parmi les clics du tracé en cours.
  //
  // La plus grossière, et non la dernière : une calibration posée à deux
  // niveaux de zoom différents ne vaut pas mieux que son pire pointage, et
  // retenir le meilleur annoncerait une précision que l'autre point ne tient
  // pas. Remise à zéro avec le tracé.
  const [resolutionDuTrace, setResolutionDuTrace] = useState<number | null>(null)
  const [textes, setTextes] = useState<TextesDePlan | null>(null)
  const [travail, setTravail] = useState<MesuresDePdf | null>(null)
  const [erreur, setErreur] = useState<unknown>(null)
  const [occupe, setOccupe] = useState(false)
  // La mesure dont on veut VÉRIFIER le tracé. Elle n'est pas « la dernière » :
  // on sélectionne aussi une mesure ancienne pour la relire, et c'est le seul
  // moyen de contrôler ce qui a été pointé.
  const [mesureVisee, setMesureVisee] = useState<string | null>(null)

  const apercuDisponible = plan.apercus.includes(page)

  // L'identité de ce que CHAQUE image doit montrer. C'est elle qui autorise le
  // pointage : tant que l'image affichée ne la porte pas, on ne clique pas.
  const cleDeLApercu = `apercu|${revisionId}|${page}`
  const cleDeLaLoupe = `loupe|${revisionId}|${page}|${loupe?.join(',') ?? ''}`

  const apercu = useRenduAuthentifie(
    async () => ({
      blob: await api.renduDeLaPage(documentId, revisionId, page),
      // Un aperçu couvre la page entière, par construction.
      zone: [0, 0, 1, 1] as [number, number, number, number],
      zoneDeclaree: true,
    }),
    apercuDisponible,
    cleDeLApercu,
  )
  const tuile = useRenduAuthentifie(
    () => api.tuileDuPlan(documentId, revisionId, page, loupe!),
    loupe !== null,
    cleDeLaLoupe,
  )

  /** L'aperçu affiché est-il bien celui de la page courante ? */
  const apercuPret = apercu.rendu?.cle === cleDeLApercu
  /** La loupe affichée montre-t-elle bien la zone courante ? */
  const loupePrete = loupe !== null && tuile.rendu?.cle === cleDeLaLoupe

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

  // **Les bordereaux du chantier et les unités du moteur**, chargés une fois.
  //
  // Une fois, et non par ligne de mesure : une page de plan en porte des
  // dizaines, et autant d'appels identiques. Un échec n'est pas relayé — sans
  // bordereau, la commande de reprise ne s'affiche simplement pas, ce qui est
  // le bon comportement quand le chantier n'en a aucun.
  const [bordereaux, setBordereaux] = useState<Boq[]>([])
  const [unites, setUnites] = useState<UniteConnue[]>([])
  useEffect(() => {
    if (!peutReprendre) return
    let abandonne = false
    api
      .boqs(projectId)
      .then((lus) => !abandonne && setBordereaux(lus))
      .catch(() => undefined)
    api
      .unites()
      .then((lues) => !abandonne && setUnites(lues))
      .catch(() => undefined)
    return () => {
      abandonne = true
    }
  }, [projectId, peutReprendre])

  /** Les deux dimensions de la page courante, en points PostScript. */
  const largeurDeLaPage = plan.dimensions_des_pages[page - 1]?.[0] ?? 0
  const hauteurDeLaPage = plan.dimensions_des_pages[page - 1]?.[1] ?? 0

  // Changer d'outil ou de page abandonne le tracé en cours : garder des points
  // désignés à un autre endroit produirait une mesure que personne n'a voulue.
  useEffect(() => {
    setPoints([])
    setResolutionDuTrace(null)
  }, [outil, page])

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
    // Rapporté à l'IMAGE, et non à son enveloppe : celle-ci porte une bordure
    // d'un pixel, qui décalait chaque clic et le mettait à une échelle de
    // 0,3 % trop grande.
    if (!apercuPret) return
    const fraction = fractionDansLImage(evenement)
    if (!fraction) return
    const { fx: x, fy: y } = fraction
    const demi = TAILLE_DE_LA_LOUPE / 2
    setLoupe([
      Math.max(0, x - demi),
      Math.max(0, y - demi),
      Math.min(1, x + demi),
      Math.min(1, y + demi),
    ])
  }

  function cliquerDansLaLoupe(evenement: React.MouseEvent<HTMLDivElement>) {
    if (outil === 'naviguer') return
    // **Le pointage n'est autorisé que sur l'image de la zone COURANTE.**
    //
    // Pendant le chargement d'une nouvelle loupe, la zone a déjà changé.
    // Accepter un clic reviendrait à enregistrer un point calculé sur la
    // nouvelle fenêtre alors que l'utilisateur visait ce que montrait
    // l'ancienne image — un point posé ailleurs qu'où il a été vu.
    if (!loupePrete || !tuile.rendu) return
    const fraction = fractionDansLImage(evenement)
    if (!fraction) return
    // De l'image vers la page : on emploie la zone que l'image couvre
    // VRAIMENT, et non celle qu'on a demandée. Un bitmap se compte en pixels
    // entiers, et l'écart — 0,2 % en largeur, 0,28 % en hauteur sur la loupe
    // mesurée — est systématique.
    const [zx0, zy0, zx1, zy1] = tuile.rendu.zone
    const point = {
      x: zx0 + fraction.fx * (zx1 - zx0),
      y: zy0 + fraction.fy * (zy1 - zy0),
    }
    const image = evenement.currentTarget.querySelector('img')
    const resolution = image
      ? resolutionAffichee(image, tuile.rendu.zone, largeurDeLaPage, hauteurDeLaPage)
      : null

    const maximum = outil === 'calibrer' ? 2 : 200
    setPoints((anciens) => (anciens.length >= maximum ? [point] : [...anciens, point]))
    setResolutionDuTrace((ancienne) => {
      if (resolution === null) return ancienne
      const recommence = points.length >= maximum
      if (recommence || ancienne === null) return resolution
      return Math.max(ancienne, resolution)
    })
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
          {apercu.rendu && (
            <div className="pdf-apercu" data-testid="pdf-apercu" onClick={cliquerSurLApercu}>
              {/* Chargé comme IMAGE, jamais en ligne : un PNG est inerte, et le
                  rester explicitement vaut mieux que le rester par hasard. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={apercu.rendu.url}
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
              rendu={tuile.rendu}
              prete={loupePrete}
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
                    // Mesurée au clic, jamais supposée. `?? 1` est le défaut
                    // PESSIMISTE du serveur — un point de papier par pixel —
                    // et il n'est atteint que si l'image a disparu entre le
                    // clic et l'envoi.
                    resolution_du_pointage: String(resolutionDuTrace ?? 1),
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
                      // L'incertitude de TRACÉ est celle de ces clics-ci, et
                      // non de ceux de la calibration. Les confondre était une
                      // hypothèse vraie dans cet écran et fausse partout
                      // ailleurs.
                      resolution_du_pointage: String(resolutionDuTrace ?? 1),
                    })
                    setPoints([])
                    setResolutionDuTrace(null)
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
        projectId={projectId}
        bordereaux={peutReprendre ? bordereaux : []}
        unites={unites}
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
        {/*
          **Les FAITS de l'extraction, et non un verdict.** L'écran annonçait
          « aucun — plan probablement scanné » dès que l'extraction rendait
          moins de cinquante caractères : le plan de bâtiment du dépôt en porte
          vingt-sept et quatre tracés vectoriels, et n'a jamais été un scan.

          Ce qui distingue un scan n'est pas la quantité de texte, c'est son
          absence totale sur une page qui ne porte qu'une image. L'écran dit
          donc ce qu'il a obtenu, et ne conclut que dans ce cas-là.
        */}
        <dd data-testid="pdf-textes-entete">
          {plan.fragments_lus > 0 ? plan.fragments_lus : t('plan.pdf.sansTexte')}
          <br />
          <span className="muted" data-testid="pdf-extraction">
            {plan.probablement_scanne
              ? t('plan.pdf.scanne')
              : !plan.porte_du_texte
                ? t('plan.pdf.sansTexteMaisVectoriel')
                : t('plan.pdf.extraction')
                    .replace('{fragments}', String(plan.fragments_lus))
                    .replace('{caracteres}', String(plan.caracteres_extraits))
                    .replace('{traces}', String(plan.traces_vectoriels))}
          </span>
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
  rendu,
  prete,
  enCours,
  erreur,
  points,
  selectionnee,
  loupe,
  outil,
  onCliquer,
  onFermer,
}: {
  rendu: RenduAffiche | null
  /**
   * L'image affichée est-elle bien celle de la zone courante ?
   *
   * Faux pendant qu'une nouvelle loupe charge, et après un échec. L'écran
   * annonce alors qu'on ne peut pas pointer, au lieu de laisser croire que
   * le dessin visible est celui qu'on désignera.
   */
  prete: boolean
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
      {rendu && (
        <div
          className={prete ? 'pdf-loupe-image' : 'pdf-loupe-image en-attente'}
          data-testid="pdf-loupe-image"
          data-prete={prete ? 'oui' : 'non'}
          data-zone={rendu.zone.map((valeur) => valeur.toFixed(10)).join(',')}
          data-zone-declaree={rendu.zoneDeclaree ? 'oui' : 'non'}
          onClick={onCliquer}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={rendu.url} alt={t('plan.pdf.loupeAlt')} data-testid="pdf-loupe-rendu" />
          <svg className="pdf-surlignages" viewBox="0 0 1 1" preserveAspectRatio="none">
            {selectionnee && (
              <polyline
                className="pdf-mesure selectionnee"
                data-testid="pdf-loupe-trace-mesure"
                points={fermerSiSurface(selectionnee.points, selectionnee.type)
                  .map(
                    (p) =>
                      `${(p.x - rendu.zone[0]) / (rendu.zone[2] - rendu.zone[0])},` +
                      `${(p.y - rendu.zone[1]) / (rendu.zone[3] - rendu.zone[1])}`,
                  )
                  .join(' ')}
              />
            )}
            {selectionnee?.points.map((point, rang) => (
              <circle
                key={`sl${rang}`}
                className="pdf-sommet"
                data-testid="pdf-loupe-sommet"
                cx={(point.x - rendu.zone[0]) / (rendu.zone[2] - rendu.zone[0])}
                cy={(point.y - rendu.zone[1]) / (rendu.zone[3] - rendu.zone[1])}
                r={0.014}
              />
            ))}
            {points.map((point, rang) => (
              <circle
                key={rang}
                className="pdf-point"
                cx={(point.x - rendu.zone[0]) / (rendu.zone[2] - rendu.zone[0])}
                cy={(point.y - rendu.zone[1]) / (rendu.zone[3] - rendu.zone[1])}
                r={0.012}
              />
            ))}
            {points.length >= 2 && (
              <polyline
                className="pdf-trace"
                points={fermerSiSurface(points, outil)
                  .map(
                    (p) =>
                      `${(p.x - rendu.zone[0]) / (rendu.zone[2] - rendu.zone[0])},` +
                      `${(p.y - rendu.zone[1]) / (rendu.zone[3] - rendu.zone[1])}`,
                  )
                  .join(' ')}
              />
            )}
          </svg>
        </div>
      )}
      {/* Dit, et non deviné : pendant le chargement, le clic est refusé, et il
          vaut mieux l'annoncer que de laisser l'utilisateur croire qu'il a
          posé un point. */}
      {!prete && !enCours && !erreur && (
        <p className="muted" data-testid="pdf-loupe-non-pointable">
          {t('plan.pdf.loupePasPrete')}
        </p>
      )}
      {rendu && !rendu.zoneDeclaree && (
        <div className="notice warning" role="note" data-testid="pdf-zone-non-declaree">
          {t('plan.pdf.zoneNonDeclaree')}
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
  projectId,
  bordereaux,
  unites,
  onDecidee,
  onMontrer,
}: {
  mesures: MesureDePdf[]
  selectionnee: string | null
  peutValider: boolean
  projectId: string
  /** Vide quand le compte ne peut pas écrire dans un bordereau, ou quand le
      chantier n'en porte aucun. Dans les deux cas, aucune commande de reprise
      n'est offerte — proposer un geste qui ne peut pas aboutir n'apprend rien. */
  bordereaux: Boq[]
  unites: UniteConnue[]
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
            projectId={projectId}
            bordereaux={bordereaux}
            unites={unites}
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
  projectId,
  bordereaux,
  unites,
  onDecidee,
  onMontrer,
}: {
  mesure: MesureDePdf
  selectionnee: boolean
  peutValider: boolean
  projectId: string
  bordereaux: Boq[]
  unites: UniteConnue[]
  onDecidee: () => void
  onMontrer: () => void
}) {
  const [correction, setCorrection] = useState('')
  const [motif, setMotif] = useState('')
  const [occupe, setOccupe] = useState(false)
  const [erreur, setErreur] = useState<unknown>(null)
  // Le formulaire de reprise est REPLIÉ par défaut. Déplié, il occupe une
  // seconde ligne du tableau : une mesure en porte déjà deux colonnes de
  // nombres et six commandes, et y glisser quatre champs de plus rendrait la
  // ligne illisible pour un geste qu'on ne fait qu'une fois par mesure.
  const [reprise, setReprise] = useState(false)
  // Ce que la reprise a écrit, quand elle a eu lieu. Gardé ICI plutôt que
  // relu du serveur : l'écran de plan ne liste pas les bordereaux, et
  // recharger tout un chantier pour afficher une phrase coûterait plus que de
  // garder ce que la réponse vient de rendre.
  const [repriseFaite, setRepriseFaite] = useState<{ ligne: BoqItem; bordereau: Boq } | null>(null)

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

  const peutEtreReprise = mesure.reprenable && bordereaux.length > 0 && repriseFaite === null

  return (
    <>
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

        {/*
          **La reprise dans un bordereau** — proposée seulement quand elle peut
          aboutir : il faut une mesure tranchée dans le bon sens, un bordereau
          où écrire, et le droit d'y écrire. Un bouton grisé n'expliquerait pas
          lequel des trois manque ; la colonne « retenue » le dit déjà, ligne
          par ligne.
        */}
        {peutEtreReprise && !reprise && (
          <button
            type="button"
            data-testid="pdf-ouvrir-reprise"
            onClick={() => setReprise(true)}
          >
            {t('plan.pdf.reprendre')}
          </button>
        )}
        {repriseFaite && (
          <p className="muted" data-testid="pdf-reprise-faite">
            {t('plan.pdf.repriseFaite')
              .replace('{poste}', repriseFaite.ligne.position)
              .replace('{bordereau}', repriseFaite.bordereau.name)}{' '}
            <Link href={`/projets/${projectId}`} data-testid="pdf-voir-le-bordereau">
              {t('plan.pdf.voirLeBordereau')}
            </Link>
          </p>
        )}
      </td>
    </tr>

    {reprise && (
      <tr data-testid="pdf-ligne-de-reprise">
        <td colSpan={6}>
          <FormulaireDeReprise
            mesure={mesure}
            bordereaux={bordereaux}
            unites={unites}
            onAnnuler={() => setReprise(false)}
            onReprise={(ligne, bordereau) => {
              setReprise(false)
              setRepriseFaite({ ligne, bordereau })
            }}
          />
        </td>
      </tr>
    )}
    </>
  )
}

/**
 * Reprendre une mesure tranchée dans une ligne de bordereau.
 *
 * **Ce que ce formulaire ne demande PAS, et c'est l'essentiel : la quantité.**
 * Elle est lue de la mesure et de la décision humaine qui l'a retenue. La
 * laisser saisir ici rendrait possible une ligne qui annonce une provenance et
 * porte un autre nombre — pire qu'une ligne sans provenance, parce qu'elle a
 * l'air sourcée.
 *
 * **Le nombre est MONTRÉ avant d'être écrit**, et c'est le serveur qui le
 * calcule : `POST …/reprises-de-mesure/apercu` n'écrit rien et rend la
 * quantité, son écriture lisible et sa provenance. Refaire la conversion ici —
 * un facteur mille entre millimètres et mètres — poserait un second calcul en
 * TypeScript, qui divergerait du premier au premier arrondi. C'est le patron
 * « prévisualiser puis confirmer » que le dépôt applique déjà à l'import de
 * prix et aux sous-détails.
 *
 * **Les unités offertes sont celles de la MÊME dimension.** Une longueur se
 * reprend en millimètres, centimètres ou mètres ; une surface en mètres carrés
 * ou en ares. Proposer un mètre linéaire pour une surface afficherait un choix
 * que le serveur refuse ensuite, et la dimension est précisément l'erreur qui
 * ne se voit pas sur le nombre — elle se voit sur le total, des semaines plus
 * tard.
 */
/** « 6 001,2 » → « 6001.2 » : espaces de milliers retirés, virgule belge en point. */
function normaliserLaSaisie(texte: string): string {
  return texte.replace(/[\s\u00a0]/g, '').replace(',', '.')
}

function FormulaireDeReprise({
  mesure,
  bordereaux,
  unites,
  onAnnuler,
  onReprise,
}: {
  mesure: MesureDePdf
  bordereaux: Boq[]
  unites: UniteConnue[]
  onAnnuler: () => void
  onReprise: (ligne: BoqItem, bordereau: Boq) => void
}) {
  const [bordereauId, setBordereauId] = useState(bordereaux[0]?.id ?? '')
  const [uniteCible, setUniteCible] = useState(mesure.unite_retenue ?? mesure.unite)
  const [position, setPosition] = useState('')
  const [designation, setDesignation] = useState(mesure.libelle)
  const [apercu, setApercu] = useState<ApercuDeReprise | null>(null)
  // Ce que la personne retient, tel qu'elle le tape — virgule belge admise.
  // Vide tant que le serveur n'a pas proposé : le champ se remplit de la
  // proposition à la première réponse, et jamais plus ensuite.
  const [quantiteRetenue, setQuantiteRetenue] = useState('')
  // La proposition du serveur, et POUR QUELS bordereau et unité elle vaut : une
  // quantité tapée en millimètres n'a aucun sens une fois l'unité passée en
  // mètres, et l'envoyer ferait refuser « 6 001,2 m » — mesuré.
  const [proposition, setProposition] = useState<{ cle: string; valeur: string } | null>(null)
  const [occupe, setOccupe] = useState(false)
  const [erreur, setErreur] = useState<unknown>(null)

  const uniteDeLaMesure = mesure.unite_retenue ?? mesure.unite
  const dimension = useMemo(
    () => unites.find((u) => u.code === uniteDeLaMesure)?.dimension ?? null,
    [unites, uniteDeLaMesure],
  )
  const unitesCompatibles = useMemo(
    () => (dimension ? unites.filter((u) => u.dimension === dimension) : []),
    [unites, dimension],
  )

  // L'aperçu se redemande à chaque changement de bordereau ou d'unité, et une
  // réponse en retard ne remplace jamais une plus récente : deux clics rapides
  // sur le sélecteur d'unité afficheraient sinon la quantité de l'avant-dernier
  // choix, en se croyant à jour. Même règle que pour les tuiles de plan.
  //
  // **La quantité retenue passe par le même aperçu.** Le navigateur ne juge
  // pas si « 6,38 » est une écriture de « 6,3787950927 ± 0,041 » : il envoie
  // ce qui est tapé, et c'est le serveur qui répond par le nombre qui sera
  // écrit — ou par un refus, affiché tel quel. Une nouvelle proposition du
  // serveur — autre unité, autre bordereau — remplace la saisie.
  useEffect(() => {
    if (!bordereauId) return
    let abandonne = false
    // L'aperçu précédent reste affiché le temps de la réponse : la brute et
    // son ± ne clignotent pas à chaque frappe. Une réponse en retard est
    // ignorée (`abandonne`), donc rien de périmé ne remplace du plus récent.
    setErreur(null)
    // La proposition dépend de l'UNITÉ, pas du bordereau : changer de
    // bordereau garde ce que la personne a tapé.
    const cle = uniteCible
    const saisie = normaliserLaSaisie(quantiteRetenue)
    const saisieValable = proposition !== null && proposition.cle === cle && saisie !== ''
    api
      .apercuDeReprise(bordereauId, {
        proposal_id: mesure.proposal_id,
        unite_cible: uniteCible,
        ...(saisieValable ? { quantite_retenue: saisie } : {}),
      })
      .then((lu) => {
        if (abandonne) return
        setApercu(lu)
        if (proposition === null || proposition.cle !== cle) {
          setProposition({ cle, valeur: lu.quantite_proposee })
          setQuantiteRetenue(lu.quantite_proposee.replace('.', ','))
        }
      })
      .catch((cause) => {
        if (abandonne) return
        // Un refus laisse la brute visible, mais aucune quantité « qui sera
        // écrite » : le bouton Reprendre se ferme avec l'aperçu.
        setApercu(null)
        setErreur(cause)
      })
    return () => {
      abandonne = true
    }
    // `proposition` est posée PAR cet effet : la relire ici le ferait boucler.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bordereauId, uniteCible, mesure.proposal_id, quantiteRetenue])

  async function reprendre(evenement: React.FormEvent) {
    evenement.preventDefault()
    const bordereau = bordereaux.find((b) => b.id === bordereauId)
    if (!bordereau) return
    setOccupe(true)
    setErreur(null)
    try {
      const ligne = await api.reprendreUneMesure(bordereau.id, {
        proposal_id: mesure.proposal_id,
        position: position.trim(),
        designation: designation.trim(),
        unite_cible: uniteCible,
        // Exactement ce que l'aperçu a jugé : la ligne portera le nombre montré.
        // Un champ vide n'est pas une quantité : il ne part pas, comme pour l'aperçu.
        ...(normaliserLaSaisie(quantiteRetenue) !== ''
          ? { quantite_retenue: normaliserLaSaisie(quantiteRetenue) }
          : {}),
      })
      onReprise(ligne, bordereau)
    } catch (cause) {
      setErreur(cause)
    } finally {
      setOccupe(false)
    }
  }

  return (
    <form className="pdf-reprise" data-testid="pdf-formulaire-reprise" onSubmit={reprendre}>
      <h4>{t('plan.pdf.reprendreTitre')}</h4>
      <p className="muted">{t('plan.pdf.reprendreAide')}</p>
      <div id={`reprise-refus-${mesure.proposal_id}`}>
        <ErrorNotice error={erreur} />
      </div>

      <div className="row">
        <div className="field">
          <label htmlFor={`reprise-boq-${mesure.proposal_id}`}>{t('plan.pdf.bordereau')}</label>
          <select
            id={`reprise-boq-${mesure.proposal_id}`}
            data-testid="pdf-reprise-bordereau"
            value={bordereauId}
            onChange={(e) => setBordereauId(e.target.value)}
          >
            {bordereaux.map((bordereau) => (
              <option key={bordereau.id} value={bordereau.id}>
                {bordereau.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`reprise-unite-${mesure.proposal_id}`}>{t('plan.pdf.uniteCible')}</label>
          <select
            id={`reprise-unite-${mesure.proposal_id}`}
            data-testid="pdf-reprise-unite"
            value={uniteCible}
            onChange={(e) => setUniteCible(e.target.value)}
          >
            {(unitesCompatibles.length > 0
              ? unitesCompatibles
              : [{ code: uniteDeLaMesure, label: uniteDeLaMesure } as UniteConnue]
            ).map((unite) => (
              <option key={unite.code} value={unite.code}>
                {unite.label} ({unite.code})
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`reprise-position-${mesure.proposal_id}`}>{t('boq.position')}</label>
          <input
            id={`reprise-position-${mesure.proposal_id}`}
            data-testid="pdf-reprise-position"
            value={position}
            onChange={(e) => setPosition(e.target.value)}
            required
          />
        </div>
        <div className="field" style={{ flex: '3 1 260px' }}>
          <label htmlFor={`reprise-designation-${mesure.proposal_id}`}>
            {t('boq.designation')}
          </label>
          <input
            id={`reprise-designation-${mesure.proposal_id}`}
            data-testid="pdf-reprise-designation"
            value={designation}
            onChange={(e) => setDesignation(e.target.value)}
            required
          />
        </div>
      </div>

      {/*
        **Ce qui sera écrit, avant de l'écrire.** La mesure brute et son ±
        restent visibles ; le serveur propose la quantité à la finesse du ±,
        et la personne peut en retenir une autre écriture — dans le ±. Au-delà,
        le serveur refuse et le dit ici, et rien ne s'écrit.
      */}
      <dl className="pdf-apercu-reprise" data-testid="pdf-apercu-reprise">
        <div>
          <dt>{t('plan.pdf.mesureBrute')}</dt>
          <dd data-testid="pdf-apercu-brute">
            {apercu ? (
              <>
                {apercu.quantite_brute_lisible}
                {apercu.incertitude_lisible ? ` ${apercu.incertitude_lisible}` : ''}
              </>
            ) : (
              <span className="muted">{t('common.loading')}</span>
            )}
          </dd>
        </div>
        <div>
          <dt>
            <label htmlFor={`reprise-quantite-${mesure.proposal_id}`}>
              {t('plan.pdf.quantiteRetenue')}
            </label>
          </dt>
          <dd>
            <input
              id={`reprise-quantite-${mesure.proposal_id}`}
              data-testid="pdf-reprise-quantite"
              inputMode="decimal"
              value={quantiteRetenue}
              onChange={(e) => setQuantiteRetenue(e.target.value)}
              disabled={proposition === null}
              aria-describedby={`reprise-quantite-unite-${mesure.proposal_id} reprise-quantite-aide-${mesure.proposal_id} reprise-refus-${mesure.proposal_id}`}
            />{' '}
            <span id={`reprise-quantite-unite-${mesure.proposal_id}`} className="mono">
              {apercu ? uniteLisible(apercu.unite) : ''}
            </span>
            <p id={`reprise-quantite-aide-${mesure.proposal_id}`} className="muted plan-faits">
              {apercu && apercu.incertitude === null
                ? t('plan.pdf.quantiteRetenueAideCorrigee')
                : t('plan.pdf.quantiteRetenueAide')}
            </p>
          </dd>
        </div>
        <div>
          <dt>{t('plan.pdf.quantiteReprise')}</dt>
          <dd data-testid="pdf-apercu-quantite">
            {apercu ? (
              <strong>{apercu.quantite_lisible}</strong>
            ) : (
              <span className="muted">{t('common.loading')}</span>
            )}
          </dd>
        </div>
        <div>
          <dt>{t('plan.pdf.provenance')}</dt>
          <dd className="muted" data-testid="pdf-apercu-provenance">
            {apercu?.provenance_lisible ?? '—'}
          </dd>
        </div>
      </dl>

      <button
        className="primary"
        type="submit"
        data-testid="pdf-reprendre"
        disabled={occupe || apercu === null || !position.trim() || !designation.trim()}
      >
        {occupe ? t('common.saving') : t('plan.pdf.reprendreConfirmer')}
      </button>
      <button type="button" data-testid="pdf-reprise-annuler" onClick={onAnnuler}>
        {t('common.cancel')}
      </button>
    </form>
  )
}
