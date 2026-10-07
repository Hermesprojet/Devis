'use client'

import Link from 'next/link'
import { useParams, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'

import { Loading } from '@/components/Feedback'
import { LecturePlan } from '@/components/LecturePlan'
import { Shell } from '@/components/Shell'
import { t } from '@/lib/i18n'

/**
 * La lecture d'un plan : un écran à elle, pas un dépliage dans la liste des
 * documents.
 *
 * **Pourquoi une route dédiée, et pas un dépliage sur place.** Trois raisons,
 * dans l'ordre où elles pèsent :
 *
 *  1. **Ce qui s'y affiche ne tient pas dans une cellule.** Le rendu du plan
 *     est un conteneur à `overflow: hidden` de 460 px, dont le contenu porte un
 *     `transform` et capte le pointeur. Le glisser à l'intérieur d'un
 *     `<td colSpan>` d'un tableau lui-même imbriqué dans un autre tableau —
 *     c'est la structure de l'historique des révisions — rendrait sa mise en
 *     page dépendante de la largeur des colonnes voisines, et le glissement
 *     entrerait en concurrence avec le défilement de la page projet.
 *  2. **Une adresse se transmet, un dépliant non.** « Regarde les mesures de
 *     ce plan » est une phrase qu'un métreur dit à un conducteur de chantier.
 *     Un état déplié n'est pas adressable : il faudrait redire quel document,
 *     quelle révision, et quel bouton ouvrir.
 *  3. **Le rechargement reste borné.** Après une décision, l'écran relit le
 *     plan. Dans un dépliage, le parent rechargerait aussi les documents, les
 *     révisions, le bordereau et les études de la page projet — pour une
 *     valeur corrigée sur une cote.
 *
 * La route reste sous `/projets/…` à dessein : `Shell` surligne son menu par
 * `pathname.startsWith(link.href)`, et « Projets » doit rester l'entrée
 * active — on n'a pas quitté le chantier.
 *
 * Le chemin porte le DOCUMENT en plus de la révision parce que les routes de
 * l'API sont imbriquées sous le document. Une adresse qui ne porterait que la
 * révision obligerait cet écran à relister tous les documents du projet pour
 * retrouver son parent : un aller-retour de plus, qui peut échouer pour une
 * raison étrangère au plan.
 */
export default function PagePlan() {
  const params = useParams<{ projectId: string }>()

  return (
    <Shell>
      <h1>{t('plan.title')}</h1>
      {/*
        Le libellé ne reprend PAS « Projets » : la barre latérale porte déjà un
        lien de ce nom, et deux liens homonymes sur la même page rendent
        ambigu ce qu'« aller dans Projets » désigne — pour un lecteur d'écran
        comme pour un test.
      */}
      <p>
        <Link href={`/projets/${params.projectId}`}>← {t('plan.backToProject')}</Link>
      </p>
      {/*
        `useSearchParams` oblige à une frontière de suspension : sans elle,
        Next refuse de pré-rendre la page. Elle est posée ici, autour du seul
        morceau qui lit la requête, et non autour de l'écran entier.
      */}
      <Suspense fallback={<Loading />}>
        <PlanDeLaRoute />
      </Suspense>
    </Shell>
  )
}

function PlanDeLaRoute() {
  const params = useParams<{ projectId: string; documentId: string; revisionId: string }>()
  // Le nom du fichier n'est qu'un CONFORT d'affichage, passé par l'écran qui
  // ouvre celui-ci. L'écran ne s'en sert pour aucun appel, et son absence ne
  // l'empêche pas de fonctionner : une adresse recopiée à la main marche.
  const nomDuFichier = useSearchParams().get('fichier')

  return (
    <LecturePlan
      documentId={params.documentId}
      revisionId={params.revisionId}
      projectId={params.projectId}
      nomDuFichier={nomDuFichier ?? undefined}
    />
  )
}
