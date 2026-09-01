/**
 * Un PNG fabriqué ici, pour n'avoir aucun binaire au dépôt.
 *
 * Le parcours a besoin d'un vrai fichier image — le serveur décode les octets
 * et refuserait n'importe quoi d'autre. L'engendrer le rend lisible : on VOIT
 * quelles dimensions et quelles couleurs le test emploie, et le dépôt reste
 * sans octets opaques.
 *
 * Fictif, sans rapport avec une marque réelle.
 */

import { crc32, deflateSync } from 'node:zlib'

function morceau(type: string, corps: Buffer): Buffer {
  const longueur = Buffer.alloc(4)
  longueur.writeUInt32BE(corps.length)
  const entete = Buffer.from(type, 'ascii')
  const somme = Buffer.alloc(4)
  somme.writeUInt32BE(crc32(Buffer.concat([entete, corps])) >>> 0)
  return Buffer.concat([longueur, entete, corps, somme])
}

/**
 * Un logo RVB opaque, en damier de deux couleurs.
 *
 * `largeur` et `hauteur` sont libres : le parcours s'en sert pour éprouver
 * une forme carrée puis une forme horizontale, les deux que rencontre un
 * logotype réel.
 */
export function logoFictif(largeur = 96, hauteur = 96, teinte: [number, number, number] = [20, 90, 170]): Buffer {
  const entete = Buffer.alloc(13)
  entete.writeUInt32BE(largeur, 0)
  entete.writeUInt32BE(hauteur, 4)
  entete[8] = 8 // profondeur
  entete[9] = 2 // RVB
  entete[10] = 0
  entete[11] = 0
  entete[12] = 0 // pas d'entrelacement

  const lignes: Buffer[] = []
  for (let y = 0; y < hauteur; y++) {
    const ligne = Buffer.alloc(1 + largeur * 3)
    ligne[0] = 0 // filtre « aucun »
    for (let x = 0; x < largeur; x++) {
      const fonce = (Math.floor(x / 12) + Math.floor(y / 12)) % 2 === 0
      const [r, v, b] = fonce ? teinte : ([245, 245, 245] as const)
      ligne[1 + x * 3] = r
      ligne[2 + x * 3] = v
      ligne[3 + x * 3] = b
    }
    lignes.push(ligne)
  }

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    morceau('IHDR', entete),
    morceau('IDAT', deflateSync(Buffer.concat(lignes), { level: 9 })),
    morceau('IEND', Buffer.alloc(0)),
  ])
}
