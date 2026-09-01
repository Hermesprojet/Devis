'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'

import { enVigueur } from '@/components/TauxDeTaxe'
import { api } from '@/lib/api'
import { PERMISSIONS, can } from '@/lib/permissions'
import { usePermissions } from '@/lib/usePermissions'

/**
 * Où en est cette organisation, et que faire ensuite.
 *
 * Une organisation neuve n'a ni taux de taxe, ni bibliothèque, ni projet. Sans
 * cet encart, elle arrivait sur une liste de projets vide sans savoir que deux
 * réglages manquaient en amont — et découvrait le premier obstacle au moment
 * du gel, c'est-à-dire trop tard.
 *
 * L'encart disparaît dès que le parcours est complet : il guide, il ne
 * s'installe pas.
 */

type Etape = {
  cle: string
  titre: string
  fait: boolean
  detail: string
  lien?: { href: string; texte: string }
}

export function MiseEnRoute() {
  const [etapes, setEtapes] = useState<Etape[] | null>(null)
  const permissions = usePermissions()
  // La mise en route s'adresse à qui peut la faire. Un lecteur y verrait trois
  // tâches dont aucune ne lui est ouverte : une liste de reproches, pas un
  // guide.
  const administre = can(permissions, PERMISSIONS.orgManage)

  useEffect(() => {
    if (!administre) return
    async function evaluer() {
      try {
        // Chaque état est LU, jamais supposé : un encart qui se trompe sur ce
        // qui est déjà fait est pire qu'aucun encart.
        const [organisation, taux, livres, projets] = await Promise.all([
          api.organization().catch(() => null),
          api.taxRates().catch(() => []),
          api.priceBooks().catch(() => []),
          api.projects().catch(() => ({ items: [] as unknown[] })),
        ])
        const manques = organisation?.missing_for_issue ?? []
        const tauxActifs = taux.filter((t) => enVigueur(t))

        let prixDisponibles = 0
        const premier = livres[0]
        if (premier) {
          const versions = await api.priceBookVersions(premier.id).catch(() => [])
          const version = versions[0]
          if (version) {
            const page = await api.priceItems(version.id, '?limit=1').catch(() => null)
            prixDisponibles = page?.page.total ?? 0
          }
        }

        setEtapes([
          {
            // En PREMIER, et c'est la raison d'être de cette étape : sans
            // profil, l'émission est refusée. Un guide qui n'en parlerait pas
            // mènerait droit à ce refus, après tout le reste du travail.
            cle: 'profil',
            titre: "Compléter le profil de l'entreprise",
            fait: manques.length === 0,
            detail:
              manques.length === 0
                ? 'Vos devis porteront votre adresse et vos coordonnées.'
                : "Un devis doit dire qui l'émet et où lui répondre. Sans cela, "
                  + "l'émission sera refusée — après que tout le reste aura été fait.",
            lien: { href: '/parametres', texte: 'Réglages' },
          },
          {
            cle: 'taxe',
            titre: 'Configurer un taux de taxe',
            fait: tauxActifs.length > 0,
            detail:
              tauxActifs.length > 0
                ? `${tauxActifs.map((t) => t.code).join(', ')} en vigueur`
                : "Sans taux, un devis affichera un TTC égal au HT. Metreo n'en installe aucun : le choix vous appartient. Un brouillon d'étude reprendra le taux dès qu'il existera ; le gel, lui, le fige définitivement.",
            lien: { href: '/parametres', texte: 'Réglages' },
          },
          {
            cle: 'prix',
            titre: 'Créer une bibliothèque et au moins un prix',
            fait: prixDisponibles > 0,
            detail:
              prixDisponibles > 0
                ? `${prixDisponibles} prix disponible${prixDisponibles > 1 ? 's' : ''}`
                : 'Une ligne sans prix bloque le gel du devis.',
            lien: { href: '/bibliotheque', texte: 'Bibliothèque' },
          },
          {
            cle: 'projet',
            titre: 'Créer un projet, son bordereau et son étude de prix',
            fait: (projets.items?.length ?? 0) > 0,
            detail:
              (projets.items?.length ?? 0) > 0
                ? `${projets.items.length} projet${projets.items.length > 1 ? 's' : ''}`
                : 'Le projet porte le bordereau, que l’étude de prix chiffre.',
            lien: { href: '/projets', texte: 'Projets' },
          },
        ])
      } catch {
        setEtapes(null)
      }
    }
    void evaluer()
  }, [administre])

  if (etapes === null) return null
  const suivante = etapes.find((e) => !e.fait)
  // Plus rien à faire : l'encart disparaît. Il guide, il ne s'installe pas.
  if (!suivante) return null

  return (
    <div className="card" data-testid="mise-en-route">
      <h2 style={{ marginTop: 0 }}>Mise en route</h2>
      <p className="muted" style={{ fontSize: 13 }}>
        Ce qu&apos;il reste à faire avant de pouvoir geler un premier devis.
      </p>
      <ol style={{ margin: '12px 0', paddingLeft: 20 }}>
        {etapes.map((etape) => (
          <li key={etape.cle} style={{ marginBottom: 8 }}>
            <span className={`badge ${etape.fait ? 'success' : 'warning'}`}>
              {etape.fait ? 'fait' : 'à faire'}
            </span>{' '}
            <strong>{etape.titre}</strong>
            <div className="muted" style={{ fontSize: 12 }}>
              {etape.detail}
              {etape.lien && !etape.fait && (
                <>
                  {' '}
                  <Link href={etape.lien.href}>{etape.lien.texte}</Link>
                </>
              )}
            </div>
          </li>
        ))}
      </ol>
      <div className="notice info" role="status">
        <strong>Prochaine action :</strong> {suivante.titre}.
        {suivante.lien && (
          <>
            {' '}
            <Link href={suivante.lien.href}>Y aller</Link>
          </>
        )}
      </div>
    </div>
  )
}
