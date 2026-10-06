#!/usr/bin/env python3
"""Mesure le coût d'une tuile de détail : temps, mémoire résidente, cache.

    python scripts/mesures/mesurer_le_budget_des_tuiles.py <dossier> A|B|C

Les trois sections répondent aux trois questions de l'ADR 0008 :

  **A** — une tuile rendue EN DIRECT : combien de temps, combien de mémoire
  résidente en plus, et cette mémoire est-elle rendue après `close()` ?
  **B** — la même tuile rendue HORS PROCESSUS : combien de temps, combien le
  parent pèse-t-il ensuite, et jusqu'où le fils est-il monté ?
  **C** — la deuxième demande, celle qui vient du cache sur le volume.

**Un processus neuf par section, et ce n'est pas une commodité.** La mesure
porte sur la mémoire résidente du processus ; une section qui a déjà chargé une
page de PDF fausse la suivante. C'est l'erreur commise au premier essai, où la
section B affichait un parent à 489 Mo hérités de la section A. Lancer les
trois sections dans trois processus est donc la seule façon d'obtenir le
chiffre que l'on croit lire.

**Les plans.** Le dossier passé en argument contient des PDF **fournis par
l'exploitant**. Aucun plan n'est versionné ni n'a à l'être : un plan de
chantier appartient à un client. Les mêmes faits sans plan réel sont vérifiés
en continu par `scripts/verifier_image_de_lecture.py`, dans l'image construite,
sur les fixtures de `scripts/fabriquer_pdf_de_test.py`.
"""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import time
from pathlib import Path

#: La zone mesurée, dans le repère de l'écran, [0,1], origine en haut à gauche.
#:
#: La MÊME pour tous les plans, et au milieu de la page : comparer deux plans
#: suppose de leur demander le même travail, et le milieu d'un plan porte du
#: dessin alors qu'un bord porte souvent de la marge.
ZONE = (0.40, 0.40, 0.45, 0.45)


def rss_en_mo() -> float:
    """La mémoire résidente de CE processus, en mégaoctets.

    Lue dans `/proc/self/status` plutôt que par `resource.getrusage` : celui-ci
    rend le PIC (`ru_maxrss`), qui ne redescend jamais et ne pourrait donc pas
    répondre à « cette mémoire est-elle rendue ».
    """
    with open("/proc/self/status") as fichier:
        for ligne in fichier:
            if ligne.startswith("VmRSS:"):
                return int(ligne.split()[1]) / 1024
    return 0.0


def section_en_direct(prive: Path) -> None:
    print(f"RSS au démarrage, avant tout import de PDFium : {rss_en_mo():.0f} Mo")
    from metreo_api.services import rendu_pdf

    print(f"RSS après l'import : {rss_en_mo():.0f} Mo")
    print(
        f"{'plan':20} {'Mo PDF':>8} {'rendu':>9} {'RSS avant':>10} "
        f"{'RSS après':>10} {'delta':>8} {'tuile':>9} {'px':>10}"
    )
    for chemin in sorted(prive.glob("*.pdf")):
        avant = rss_en_mo()
        depart = time.perf_counter()
        tuile = rendu_pdf.rendre_une_zone(chemin, page=1, zone=ZONE)
        duree = time.perf_counter() - depart
        apres = rss_en_mo()
        print(
            f"{chemin.name:20} {chemin.stat().st_size / 1e6:7.1f} {duree * 1000:8.0f}ms "
            f"{avant:9.0f}Mo {apres:9.0f}Mo {apres - avant:+7.0f}Mo "
            f"{len(tuile.png) / 1024:8.1f}Ko {tuile.largeur}x{tuile.hauteur:<5}"
        )
    print(f"  -> RSS final, documents fermés : {rss_en_mo():.0f} Mo")


def section_hors_processus(prive: Path) -> None:
    print(f"RSS du parent au démarrage : {rss_en_mo():.0f} Mo")
    print(f"{'plan':20} {'durée':>9} {'sortie':>7} {'RSS parent':>11} {'pic du fils':>12}")
    for chemin in sorted(prive.glob("*.pdf")):
        sortie = Path(os.environ.get("TMPDIR", "/tmp")) / f"tuile-{chemin.stem}.png"
        depart = time.perf_counter()
        resultat = subprocess.run(
            [
                sys.executable,
                "-m",
                "metreo_api.rendu_tuile",
                "--fichier",
                str(chemin),
                "--page",
                "1",
                "--zone",
                *(f"{valeur:.6f}" for valeur in ZONE),
                "--sortie",
                str(sortie),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        duree = time.perf_counter() - depart
        # `ru_maxrss` des ENFANTS : le pic du plus lourd d'entre eux depuis le
        # début. Il ne redescend pas — c'est exactement ce qu'on veut savoir.
        pic = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
        print(
            f"{chemin.name:20} {duree * 1000:8.0f}ms {resultat.returncode:7} "
            f"{rss_en_mo():10.0f}Mo {pic:11.0f}Mo"
        )
    print(f"  -> RSS du parent après les rendus : {rss_en_mo():.0f} Mo")


def section_cache(prive: Path) -> None:
    from metreo_api.services import tuiles
    from metreo_api.services.document_storage import StockageLocal

    racine = Path(os.environ.get("TMPDIR", "/tmp")) / "volume-de-mesure"
    racine.mkdir(parents=True, exist_ok=True)
    stockage = StockageLocal(racine)
    print(f"RSS du parent au démarrage : {rss_en_mo():.0f} Mo")
    for chemin in sorted(prive.glob("*.pdf")):
        releves = []
        for _ in range(2):
            depart = time.perf_counter()
            tuile = tuiles.obtenir(
                stockage,
                organization_id="org-de-mesure",
                revision_id=chemin.stem,
                original=chemin,
                page=1,
                zone=ZONE,
            )
            releves.append((time.perf_counter() - depart, tuile.depuis_le_cache, len(tuile.png)))
        (premier, cache1, poids), (second, cache2, _) = releves
        print(
            f"    {chemin.name:20} 1re {premier * 1000:7.0f}ms (cache={cache1})  "
            f"2e {second * 1000:6.1f}ms (cache={cache2})  {poids / 1024:.1f}Ko"
        )
    print(f"  -> RSS du parent après les appels : {rss_en_mo():.0f} Mo")


SECTIONS = {"A": section_en_direct, "B": section_hors_processus, "C": section_cache}


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2 or arguments[1] not in SECTIONS:
        print(__doc__)
        return 2
    prive = Path(arguments[0])
    if not prive.is_dir():
        print(f"« {prive} » n'est pas un dossier.", file=sys.stderr)
        return 2
    SECTIONS[arguments[1]](prive)
    return 0


if __name__ == "__main__":  # pragma: no cover - outil de mesure
    raise SystemExit(main())
