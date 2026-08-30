/** Le démontage du banc, en un module séparé : Playwright veut un fichier par crochet. */
import { ranger } from './banc'

export default async function demonter(): Promise<void> {
  await ranger()
}
