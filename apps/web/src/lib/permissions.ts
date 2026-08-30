/**
 * Ce que le rôle du porteur du jeton l'autorise à faire.
 *
 * L'API reste l'autorité : elle refuse ce qu'elle doit refuser, avec le code
 * et la permission manquante. Ce module ne remplace pas ce refus, il évite de
 * proposer une commande qui ne pourra jamais aboutir — un lecteur voyait trois
 * boutons dont aucun ne marchait.
 *
 * La liste des permissions vient de `/auth/me`, donc du serveur. Elle n'est
 * pas recopiée ici : recopier `ROLE_PERMISSIONS` côté web produirait deux
 * vérités qui divergeraient au premier rôle modifié.
 */

/** Les permissions que l'interface consulte. Les noms sont ceux de l'API. */
export const PERMISSIONS = {
  exportClient: 'export:client',
  exportInternal: 'export:internal',
  estimateFreeze: 'estimate:freeze',
  auditRead: 'audit:read',
  orgManage: 'org:manage',
  userManage: 'user:manage',
  pricebookWrite: 'pricebook:write',
  boqWrite: 'boq:write',
  estimateWrite: 'estimate:write',
} as const

export type PermissionName = (typeof PERMISSIONS)[keyof typeof PERMISSIONS]

/**
 * Décide si une commande peut être proposée.
 *
 * `undefined` — le profil n'est pas encore chargé — n'autorise rien. Afficher
 * pendant le chargement puis masquer ferait clignoter la barre d'outils, et
 * afficher par défaut nous ramènerait au comportement qu'on corrige.
 */
export function can(
  permissions: readonly string[] | undefined,
  permission: PermissionName,
): boolean {
  return permissions?.includes(permission) ?? false
}

/**
 * Pourquoi une commande est indisponible, quand elle l'est.
 *
 * Deux causes distinctes, et deux traitements distincts :
 *
 *   - `'forbidden'` : le rôle ne pourra jamais l'exécuter. La commande est
 *     masquée — la montrer désactivée n'apprendrait rien d'actionnable.
 *   - `'state'` : le rôle y a droit, mais l'objet ne s'y prête pas en l'état
 *     (une version déjà gelée). La commande reste visible, désactivée, avec
 *     l'explication : l'utilisateur doit savoir qu'elle existe.
 */
export type Availability =
  | { readonly available: true }
  | { readonly available: false; readonly reason: 'forbidden' }
  | { readonly available: false; readonly reason: 'state'; readonly explanation: string }

export function availability(
  permissions: readonly string[] | undefined,
  permission: PermissionName,
  blockedByState?: string | null,
): Availability {
  if (!can(permissions, permission)) return { available: false, reason: 'forbidden' }
  if (blockedByState) {
    return { available: false, reason: 'state', explanation: blockedByState }
  }
  return { available: true }
}
