/**
 * Écrire à la belge un nombre que le SERVEUR a déjà décidé.
 *
 * **Pourquoi cette fonction a le droit d'exister côté écran.** La règle du
 * dépôt est qu'un nombre destiné à être LU est rendu par le serveur et que
 * l'écran ne le recalcule pas : deux arrondis — un en Python, un en
 * TypeScript — finiraient par diverger d'un chiffre, et c'est le genre d'écart
 * qu'on ne voit jamais venir.
 *
 * Cette fonction **ne décide rien**. Elle n'arrondit pas, ne choisit aucune
 * décimale, ne passe jamais par `Number` — ce qui lui interdirait d'exister,
 * puisqu'un flottant perd les centimes au-delà de quinze chiffres. Elle reçoit
 * la chaîne canonique rendue par l'API, celle que la `RoundingPolicy` de
 * l'entreprise a produite, et elle en change l'ORTHOGRAPHE : le point devient
 * une virgule, les milliers sont groupés. Rien d'autre.
 *
 * C'est le même geste que `services/lisible.nombre_francais_tel_quel` côté
 * serveur, et les deux doivent rendre la même chaîne — les scénarios de
 * navigateur comparent ce que l'écran affiche à ce que le PDF imprime.
 *
 * Ce qui reste rendu par le serveur, parce qu'il s'y décide quelque chose :
 * le nombre de décimales d'une mesure (il vient de son incertitude), celui
 * d'une quantité de bordereau (plancher à deux), et le symbole d'une unité
 * (« m² » là où le code dit « m2 »). Ces valeurs arrivent déjà écrites, dans
 * des champs `*_lisible`, et ne repassent pas ici.
 */

/**
 * L'espace fine insécable, U+202F : le séparateur de milliers belge.
 *
 * Insécable à dessein — un espace ordinaire laisserait « 1 250,50 » se couper
 * en deux en fin de ligne, et « 1 » seul en fin de colonne se lit comme un
 * autre nombre.
 */
export const ESPACE_FINE = ' '

/**
 * « 66 053,78 » pour « 66053.78 ». Une chaîne qui n'est pas un nombre revient
 * telle quelle : l'API rend « » pour un poste sans prix, et un libellé de
 * section passe par les mêmes colonnes qu'un montant.
 */
export function ecrireEnFrancais(texte: string | null | undefined): string {
  if (texte === null || texte === undefined || texte === '') return ''
  let signe = ''
  let reste = texte
  if (reste[0] === '-' || reste[0] === '+') {
    signe = reste[0] === '-' ? '-' : ''
    reste = reste.slice(1)
  }
  const point = reste.indexOf('.')
  const entiereBrute = point === -1 ? reste : reste.slice(0, point)
  const fraction = point === -1 ? '' : reste.slice(point + 1)
  if (!/^\d+$/.test(entiereBrute) || (point !== -1 && !/^\d+$/.test(fraction))) {
    return texte
  }

  const groupes: string[] = []
  let entiere = entiereBrute
  while (entiere.length > 3) {
    groupes.unshift(entiere.slice(-3))
    entiere = entiere.slice(0, -3)
  }
  groupes.unshift(entiere)
  const groupee = groupes.join(ESPACE_FINE)

  return fraction ? `${signe}${groupee},${fraction}` : `${signe}${groupee}`
}

/**
 * Le même nombre, suivi de sa devise. « 66 053,78 EUR ».
 *
 * Le code de devise et non le symbole : « € » devant ou derrière, avec ou sans
 * espace, est une convention qui varie d'un pays à l'autre, et le dépôt n'en a
 * tranché aucune. Le code ISO ne se trompe nulle part.
 */
export function montantEnFrancais(
  texte: string | null | undefined,
  devise: string | null | undefined,
): string {
  const nombre = ecrireEnFrancais(texte)
  if (!nombre) return ''
  return devise ? `${nombre} ${devise}` : nombre
}
