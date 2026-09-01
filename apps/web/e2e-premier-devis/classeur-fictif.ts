/**
 * Un classeur XLSX fabriqué ici, pour n'avoir aucun binaire au dépôt.
 *
 * Même raison que le PNG voisin, et même méthode. Le parcours a besoin d'un
 * VRAI classeur — le serveur ouvre l'archive, refuse ce qui n'en est pas une,
 * et lit le XML. L'engendrer le rend lisible : on voit quelles cellules et
 * quels types le test emploie, et le dépôt reste sans octets opaques.
 *
 * **Pourquoi l'écrire en TypeScript plutôt que d'appeler le script Python du
 * dépôt.** La suite interdit à tous ses fichiers, sauf au banc, de lancer un
 * programme extérieur — et elle a raison : « ce n'est qu'une fixture » est
 * exactement la justification qui finirait par laisser passer un vrai
 * raccourci. Son garde-fou lit le TEXTE des fichiers, si bien que même la
 * tournure citée ici en exemple le déclencherait ; c'est le prix d'un contrôle
 * qu'on ne peut pas contourner par mégarde, et il est bien payé. Le fichier
 * vient donc de l'extérieur, comme celui d'un fournisseur.
 *
 * Écrire un producteur OOXML n'est pas la même chose qu'écrire un ANALYSEUR :
 * le serveur, lui, lit des fichiers hostiles et s'appuie pour cela sur une
 * bibliothèque maintenue. Ici, si ce producteur se trompe, openpyxl refuse le
 * classeur et le test tombe — bruyamment, ce qui est le comportement voulu.
 *
 * Le format est réduit au strict nécessaire : chaînes en ligne plutôt que
 * table partagée, aucun style, aucune formule.
 */

import { crc32, deflateRawSync } from 'node:zlib'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

type Cellule = string | number

/** Échappe ce qui, dans un texte, casserait le XML qui le porte. */
function xml(texte: string): string {
  return texte
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** La référence d'une colonne : 1 → A, 27 → AA. */
function colonne(index: number): string {
  let reste = index
  let nom = ''
  while (reste > 0) {
    const modulo = (reste - 1) % 26
    nom = String.fromCharCode(65 + modulo) + nom
    reste = Math.floor((reste - modulo) / 26)
  }
  return nom
}

function feuilleXml(lignes: Cellule[][]): string {
  const corps = lignes
    .map((ligne, rangIndex) => {
      const rang = rangIndex + 1
      const cellules = ligne
        .map((valeur, colonneIndex) => {
          const reference = `${colonne(colonneIndex + 1)}${rang}`
          if (typeof valeur === 'number') {
            return `<c r="${reference}"><v>${valeur}</v></c>`
          }
          // Chaîne EN LIGNE : la table partagée obligerait à tenir un index,
          // pour aucun gain sur un fichier de quelques lignes.
          return `<c r="${reference}" t="inlineStr"><is><t xml:space="preserve">${xml(
            String(valeur),
          )}</t></is></c>`
        })
        .join('')
      return `<row r="${rang}">${cellules}</row>`
    })
    .join('')
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${corps}</sheetData></worksheet>`
}

/** Les entrées d'un classeur portant ces feuilles, dans cet ordre. */
function documents(feuilles: { nom: string; lignes: Cellule[][] }[]): Map<string, string> {
  const fichiers = new Map<string, string>()

  fichiers.set(
    '[Content_Types].xml',
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>${feuilles
      .map(
        (_, index) =>
          `<Override PartName="/xl/worksheets/sheet${index + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`,
      )
      .join('')}</Types>`,
  )

  fichiers.set(
    '_rels/.rels',
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
  )

  fichiers.set(
    'xl/workbook.xml',
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${feuilles
      .map(
        (feuille, index) =>
          `<sheet name="${xml(feuille.nom)}" sheetId="${index + 1}" r:id="rId${index + 1}"/>`,
      )
      .join('')}</sheets></workbook>`,
  )

  fichiers.set(
    'xl/_rels/workbook.xml.rels',
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${feuilles
      .map(
        (_, index) =>
          `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${index + 1}.xml"/>`,
      )
      .join('')}</Relationships>`,
  )

  feuilles.forEach((feuille, index) => {
    fichiers.set(`xl/worksheets/sheet${index + 1}.xml`, feuilleXml(feuille.lignes))
  })

  return fichiers
}

/** Assemble une archive ZIP : en-têtes locaux, répertoire central, fin. */
function zip(fichiers: Map<string, string>): Buffer {
  const morceaux: Buffer[] = []
  const central: Buffer[] = []
  let position = 0

  for (const [nom, contenu] of fichiers) {
    const brut = Buffer.from(contenu, 'utf8')
    const comprime = deflateRawSync(brut)
    const somme = crc32(brut) >>> 0
    const titre = Buffer.from(nom, 'utf8')

    const entete = Buffer.alloc(30)
    entete.writeUInt32LE(0x04034b50, 0) // signature d'en-tête local
    entete.writeUInt16LE(20, 4) // version minimale
    entete.writeUInt16LE(0, 6) // aucun drapeau
    entete.writeUInt16LE(8, 8) // deflate
    entete.writeUInt16LE(0, 10) // heure — fixe, pour que l'archive soit reproductible
    entete.writeUInt16LE(33, 12) // date — 1er janvier 1980
    entete.writeUInt32LE(somme, 14)
    entete.writeUInt32LE(comprime.length, 18)
    entete.writeUInt32LE(brut.length, 22)
    entete.writeUInt16LE(titre.length, 26)
    entete.writeUInt16LE(0, 28)

    morceaux.push(entete, titre, comprime)

    const fiche = Buffer.alloc(46)
    fiche.writeUInt32LE(0x02014b50, 0) // signature de répertoire central
    fiche.writeUInt16LE(20, 4)
    fiche.writeUInt16LE(20, 6)
    fiche.writeUInt16LE(0, 8)
    fiche.writeUInt16LE(8, 10)
    fiche.writeUInt16LE(0, 12)
    fiche.writeUInt16LE(33, 14)
    fiche.writeUInt32LE(somme, 16)
    fiche.writeUInt32LE(comprime.length, 20)
    fiche.writeUInt32LE(brut.length, 24)
    fiche.writeUInt16LE(titre.length, 28)
    fiche.writeUInt32LE(position, 42) // décalage de l'en-tête local
    central.push(fiche, titre)

    position += entete.length + titre.length + comprime.length
  }

  const repertoire = Buffer.concat(central)
  const fin = Buffer.alloc(22)
  fin.writeUInt32LE(0x06054b50, 0)
  fin.writeUInt16LE(fichiers.size, 8)
  fin.writeUInt16LE(fichiers.size, 10)
  fin.writeUInt32LE(repertoire.length, 12)
  fin.writeUInt32LE(position, 16)

  return Buffer.concat([...morceaux, repertoire, fin])
}

/** Le chemin d'un classeur portant les lignes demandées, sur la feuille dite. */
export function classeurFictif(
  lignes: Cellule[][],
  options: { entetes?: string[]; feuille?: string; autresFeuilles?: string[] } = {},
): string {
  const entetes = options.entetes ?? ['code', 'libelle', 'unite', 'prix_unitaire']
  const feuilles = [
    { nom: options.feuille ?? 'Prix', lignes: [entetes, ...lignes] },
    ...(options.autresFeuilles ?? []).map((nom) => ({
      nom,
      lignes: [['Rien à importer ici'] as Cellule[]],
    })),
  ]
  const dossier = mkdtempSync(join(tmpdir(), 'metreo-classeur-'))
  const cible = join(dossier, 'bareme.xlsx')
  writeFileSync(cible, zip(documents(feuilles)))
  return cible
}
