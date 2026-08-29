'use client'

import { useEffect, useState } from 'react'

import { api, loadSession } from '@/lib/api'

/**
 * Les permissions du porteur du jeton, telles que l'API les déclare.
 *
 * `/auth/me` les renvoyait déjà et `api.ts` les typait déjà ; c'était la seule
 * occurrence du champ dans tout `apps/web/src` — reçu, typé, lu nulle part.
 *
 * Le résultat est mis en cache pour la durée de l'onglet : le jeton vit dans
 * `sessionStorage`, ses permissions ne changent pas pendant sa vie, et chaque
 * écran qui les consulte ne doit pas rappeler `/auth/me`. La clé du cache est
 * le jeton lui-même : se reconnecter sous un autre rôle repart de zéro.
 */
let cache: { token: string; permissions: readonly string[] } | null = null

export function usePermissions(): readonly string[] | undefined {
  const [permissions, setPermissions] = useState<readonly string[] | undefined>(() => {
    const session = loadSession()
    return session && cache?.token === session.token ? cache.permissions : undefined
  })

  useEffect(() => {
    const session = loadSession()
    if (!session) {
      setPermissions(undefined)
      return
    }
    if (cache?.token === session.token) {
      setPermissions(cache.permissions)
      return
    }
    let active = true
    api
      .me()
      .then((profile) => {
        cache = { token: session.token, permissions: profile.permissions }
        if (active) setPermissions(profile.permissions)
      })
      .catch(() => {
        // Un échec ici n'autorise rien : `undefined` masque les commandes.
        // `Shell` traite déjà l'échec de `/auth/me` de façon visible, et
        // `request` met fin à la session sur jeton expiré.
        if (active) setPermissions(undefined)
      })
    return () => {
      active = false
    }
  }, [])

  return permissions
}

/** Vide le cache. Appelé à la déconnexion, et par les tests. */
export function forgetPermissions(): void {
  cache = null
}
