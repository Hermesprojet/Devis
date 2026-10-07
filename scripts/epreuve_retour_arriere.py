#!/usr/bin/env python3
"""La voie A du retour arrière, éprouvée : l'ANCIENNE API sur le schéma NEUF.

    python3 scripts/epreuve_retour_arriere.py [--ancienne-revision origin/main]

**Pourquoi cette épreuve existe.** `docs/MISE_EN_LIGNE_LECTURE_DE_PLANS.md`
propose trois sorties après un déploiement raté, et classe la voie A — remettre
l'ancienne image en démarrant `api` SANS le service `migrate` — comme « la plus
réversible, et la plus rapide ». C'était un raisonnement, pas une mesure : rien
ne disait que l'ancien code TOURNE sur un schéma qu'il ne connaît pas, et
encore moins que les parcours du métier y fonctionnent.

Une API qui démarre ne prouve rien. Ce qui compte est : peut-on encore se
connecter, lire ses chantiers, créer un client, chiffrer un devis, l'émettre et
l'exporter ? Et que se passe-t-il pour les données que la version neuve avait
créées ?

**Ce que le script fait, dans l'ordre.**

1. Il monte une base **neuve** au dernier schéma, l'amorce, et y joue un
   parcours PDF complet — dépôt, analyse, calibration, mesure, décision — avec
   le code de la branche courante. C'est l'état réel d'un essai interrompu.
2. Il sort un instantané de ce que la base contient.
3. Il récupère l'**ancien** code dans un arbre de travail séparé, et lance ses
   parcours métier contre la MÊME base, **sans toucher aux migrations**.
4. Il rend un tableau : ce qui marche, ce qui ne marche pas, et ce qui est
   devenu invisible.

Aucun conteneur, aucun réseau, aucune machine distante : deux processus Python
et un fichier SQLite. L'épreuve est donc rejouable par n'importe qui, en une
minute, sans rien installer de plus.

**Ce qu'elle ne couvre pas, et qu'il faut lire ailleurs.** Elle éprouve le
CODE contre le SCHÉMA. Elle ne dit rien du déclencheur PostgreSQL — SQLite et
PostgreSQL n'ont pas les mêmes — ni du comportement de `docker compose`, qui
est décrit et mesuré dans le guide de mise en ligne.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

#: La révision dont on veut éprouver le retour. `origin/main` est l'image en
#: service tant que la lecture de plans n'est pas fusionnée.
ANCIENNE_REVISION_PAR_DEFAUT = "origin/main"

#: Les variables communes aux deux côtés. `METREO_ENVIRONMENT=test` garde le
#: mode d'authentification de développement, qui est le seul qui permette de
#: se connecter sans fournisseur OIDC.
ENVIRONNEMENT_COMMUN = {
    "METREO_ENVIRONMENT": "test",
    "METREO_AUTH_MODE": "dev",
    "METREO_JWT_SECRET": "epreuve-retour-arriere-sans-valeur-0123456789",
}


# ---------------------------------------------------------------------------
# Les deux programmes joués en sous-processus
# ---------------------------------------------------------------------------

#: Côté NEUF : amorcer, déposer un plan, le mesurer, trancher.
#:
#: Écrit comme une chaîne et exécuté par `-c` plutôt que posé dans un fichier :
#: ce programme doit tourner avec le code de la branche COURANTE, et l'autre
#: avec celui de l'ancienne. Deux fichiers dans deux arbres finiraient par
#: diverger, et l'épreuve comparerait deux choses différentes.
PROGRAMME_NEUF = r"""
import json, sys
from fastapi.testclient import TestClient
from metreo_api.main import app

sys.path.insert(0, "SCRIPTS")
import fabriquer_pdf_de_test as fabrique

client = TestClient(app)
jeton = client.post("/api/v1/auth/dev-login", json={"email": "admin@dubois.demo"})
jeton.raise_for_status()
entetes = {"Authorization": "Bearer " + jeton.json()["access_token"]}

projet = client.post("/api/v1/projects", headers=entetes,
                     json={"reference": "RETOUR-001", "name": "Chantier du retour arriere"})
projet.raise_for_status()
document = client.post(f"/api/v1/projects/{projet.json()['id']}/documents",
                       headers=entetes, json={"title": "Plan RDC"})
document.raise_for_status()
document_id = document.json()["id"]

revision = client.post(f"/api/v1/documents/{document_id}/revisions", headers=entetes,
                       files={"file": ("plan.pdf", fabrique.plan_de_batiment(), "application/pdf")})
revision.raise_for_status()
revision_id = revision.json()["id"]

base = f"/api/v1/documents/{document_id}/revisions/{revision_id}/plan"
client.post(f"{base}/analyse", headers=entetes).raise_for_status()
client.post(f"{base}/calibration", headers=entetes, json={
    "page": 1,
    "premier": {"x": 0.1428, "y": 0.875},
    "second": {"x": 0.6190, "y": 0.875},
    "distance_reelle": "5000", "unite": "mm",
    "resolution_du_pointage": "0.041",
    "motif": "Cote 5000 du plan RDC",
}).raise_for_status()
mesure = client.post(f"{base}/mesures", headers=entetes, json={
    "page": 1, "type": "segment",
    "points": [{"x": 0.1428, "y": 0.75}, {"x": 0.7142, "y": 0.75}],
    "libelle": "Mur avant",
})
mesure.raise_for_status()
proposition = mesure.json()["proposal_id"]
client.post(f"/api/v1/extraction-proposals/{proposition}/decisions", headers=entetes,
            json={"decision": "accepted", "reason": "Verifiee sur place."}).raise_for_status()

print("@@ETAT@@" + json.dumps({
    "document_id": document_id,
    "revision_id": revision_id,
    "projet_id": projet.json()["id"],
    "proposal_id": proposition,
    "mesure_lisible": mesure.json()["valeur_lisible"],
}))
"""

#: Côté ANCIEN : les parcours du métier, un par un, sur la même base.
#:
#: Chacun est joué isolément et son échec est CAPTURÉ : l'épreuve doit rendre
#: un tableau complet, pas s'arrêter au premier refus. C'est la différence
#: entre « ça ne marche pas » et « voici les quatre choses qui ne marchent
#: plus, et les onze qui marchent ».
PROGRAMME_ANCIEN = r"""
import json, traceback
from fastapi.testclient import TestClient
from metreo_api.main import app

ETAT = json.loads(r'''@@JSON@@''')
# Un vrai PDF minimal : l'ancienne version renifle le contenu et refuse un
# `text/plain` qui ressemble a du CSV. Ce n'est pas un defaut de retour
# arriere, c'est le controle de type qui fait son travail.
NOTE_PDF = bytes.fromhex("255044462d312e340a312030206f626a0a3c3c202f54797065202f436174616c6f67202f5061676573203220302052203e3e0a656e646f626a0a322030206f626a0a3c3c202f54797065202f5061676573202f4b696473205b33203020525d202f436f756e742031203e3e0a656e646f626a0a332030206f626a0a3c3c202f54797065202f50616765202f506172656e74203220302052202f4d65646961426f78205b30203020323030203130305d202f5265736f7572636573203c3c202f466f6e74203c3c202f4631203420302052203e3e203e3e202f436f6e74656e7473203520302052203e3e0a656e646f626a0a342030206f626a0a3c3c202f54797065202f466f6e74202f53756274797065202f5479706531202f42617365466f6e74202f48656c766574696361203e3e0a656e646f626a0a352030206f626a0a3c3c202f4c656e677468203335203e3e0a73747265616d0a4254202f46312031322054662032302038302054642028353030302920546a2045540a0a656e6473747265616d0a656e646f626a0a787265660a3020360a303030303030303030302036353533352066200a30303030303030303039203030303030206e200a30303030303030303538203030303030206e200a30303030303030313135203030303030206e200a30303030303030323431203030303030206e200a30303030303030333131203030303030206e200a747261696c65720a3c3c202f53697a652036202f526f6f74203120302052203e3e0a7374617274787265660a3339360a2525454f460a")
resultats = []

def essai(nom, attendu, fonction):
    try:
        constat = fonction()
    except Exception as erreur:
        resultats.append({"parcours": nom, "attendu": attendu, "etat": "ERREUR",
                          "detail": f"{type(erreur).__name__}: {erreur}"[:300]})
        return None
    resultats.append({"parcours": nom, "attendu": attendu, **constat})
    return constat.get("valeur")

client = TestClient(app)

def connexion():
    r = client.post("/api/v1/auth/dev-login", json={"email": "admin@dubois.demo"})
    return {"etat": "OK" if r.status_code == 200 else "REFUS",
            "detail": f"HTTP {r.status_code}", "valeur": r.json().get("access_token") if r.status_code == 200 else None}

jeton = essai("Se connecter", "la session s'ouvre", connexion)
entetes = {"Authorization": f"Bearer {jeton}"} if jeton else {}

def lecture(nom, chemin, attendu):
    def faire():
        r = client.get(chemin, headers=entetes)
        return {"etat": "OK" if r.status_code == 200 else "REFUS", "detail": f"HTTP {r.status_code}"}
    essai(nom, attendu, faire)

lecture("Lire son profil", "/api/v1/auth/me", "role et permissions")
lecture("Lister les chantiers", "/api/v1/projects", "le chantier cree par la version neuve est la")
lecture("Lister les clients", "/api/v1/clients", "le repertoire repond")
lecture("Lister la bibliotheque", "/api/v1/price-books", "les prix repondent")
lecture("Ouvrir le chantier neuf", f"/api/v1/projects/{ETAT['projet_id']}", "le chantier s'ouvre")
lecture("Lister ses documents", f"/api/v1/projects/{ETAT['projet_id']}/documents", "le plan deverse est visible")
lecture("Relire le document", f"/api/v1/documents/{ETAT['document_id']}", "les metadonnees repondent")
lecture("Telecharger l'original du plan",
        f"/api/v1/documents/{ETAT['document_id']}/revisions/{ETAT['revision_id']}/content",
        "les octets deposes sont encore servis")
lecture("Lire le plan (ecran neuf)",
        f"/api/v1/documents/{ETAT['document_id']}/revisions/{ETAT['revision_id']}/plan",
        "ABSENTE de l'ancienne version : un 404 est le bon comportement")
lecture("Relire les mesures du PDF",
        f"/api/v1/documents/{ETAT['document_id']}/revisions/{ETAT['revision_id']}/plan/mesures",
        "ABSENTE de l'ancienne version")
lecture("Journal d'audit", "/api/v1/audit/events", "les evenements ecrits par la version neuve se lisent")
lecture("Verifier la chaine (par l'API)", "/api/v1/audit/verify", "l'ancienne API valide la chaine neuve")

def creer_un_client():
    r = client.post("/api/v1/clients", headers=entetes, json={
        "name": "Client du retour arriere", "billing_address": "Rue du Test 1",
        "postal_code": "1000", "city": "Bruxelles", "country_code": "BE"})
    return {"etat": "OK" if r.status_code == 201 else "REFUS", "detail": f"HTTP {r.status_code} {r.text[:120]}",
            "valeur": r.json().get("id") if r.status_code == 201 else None}
essai("Creer un client", "l'ecriture passe", creer_un_client)

def creer_un_chantier():
    r = client.post("/api/v1/projects", headers=entetes,
                    json={"reference": "RETOUR-ANCIEN", "name": "Chantier cree par l'ancienne version"})
    return {"etat": "OK" if r.status_code == 201 else "REFUS", "detail": f"HTTP {r.status_code} {r.text[:120]}",
            "valeur": r.json().get("id") if r.status_code == 201 else None}
chantier = essai("Creer un chantier", "l'ecriture passe", creer_un_chantier)

def deposer_un_document():
    doc = client.post(f"/api/v1/projects/{chantier}/documents", headers=entetes, json={"title": "Note"})
    if doc.status_code != 201:
        return {"etat": "REFUS", "detail": f"HTTP {doc.status_code} {doc.text[:120]}"}
    r = client.post(f"/api/v1/documents/{doc.json()['id']}/revisions", headers=entetes,
                    files={"file": ("note.pdf", NOTE_PDF, "application/pdf")})
    return {"etat": "OK" if r.status_code == 201 else "REFUS", "detail": f"HTTP {r.status_code} {r.text[:160]}"}
if chantier:
    essai("Deposer un document", "le volume et la base acceptent", deposer_un_document)

def verifier_la_chaine():
    from metreo_api.db import get_session_factory
    from metreo_api.services import audit
    with get_session_factory()() as session:
        from metreo_api.models import Organization
        organisation = session.scalars(__import__("sqlalchemy").select(Organization)).first()
        rapport = audit.verify_chain(session, organization_id=organisation.id)
        intacte = rapport if isinstance(rapport, bool) else getattr(rapport, "ok", None)
        return {"etat": "OK" if intacte in (True, None) else "ROMPUE", "detail": str(rapport)[:200]}
essai("Verifier la chaine d'audit", "la chaine ecrite par la version neuve reste valide", verifier_la_chaine)

# Les mesures PDF existent en base ; l'ancienne version ne les voit pas.
# Ce n'est pas une panne : c'est exactement ce que la voie A promet. Les
# tables neuves restent, l'ancien code les ignore, et rien n'est detruit.
# Le constater vaut mieux que le supposer.
def mesures_invisibles():
    from sqlalchemy import text
    from metreo_api.db import get_session_factory
    with get_session_factory()() as session:
        propositions = session.execute(
            text("SELECT COUNT(*) FROM extraction_proposals")).scalar_one()
        calibrations = session.execute(
            text("SELECT COUNT(*) FROM plan_calibrations")).scalar_one()
        decisions = session.execute(
            text("SELECT COUNT(*) FROM validation_decisions")).scalar_one()
    intactes = propositions >= 1 and calibrations >= 1 and decisions >= 1
    return {"etat": "OK" if intactes else "PERDUES",
            "detail": f"{propositions} proposition(s), {calibrations} calibration(s), "
                      f"{decisions} decision(s) toujours en base"}
essai("Les donnees de l'essai survivent", "rien n'est detruit par le retour", mesures_invisibles)

# Et ce qui arrive si l'on OUBLIE de retirer le service `migrate`.
# C'est la voie A manquee, et elle a un cout precis : l'ancienne image ne
# trouve pas la revision courante dans son propre arbre, et sort en erreur.
# Le mesurer ici evite d'avoir a le decouvrir en production.
def migrer_quand_meme():
    import subprocess
    import sys
    fait = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                          cwd=ETAT["racine_ancienne"] + "/apps/api",
                          capture_output=True, text=True, check=False)
    sortie = (fait.stderr or "") + (fait.stdout or "")
    return {"etat": "REFUS" if fait.returncode != 0 else "OK",
            "detail": f"code {fait.returncode} : " + sortie.strip().splitlines()[-1][:160]
                      if sortie.strip() else f"code {fait.returncode}"}
essai("Relancer les migrations (voie A manquee)",
      "l'ancienne image NE DOIT PAS pouvoir monter le schema", migrer_quand_meme)

print("@@RESULTATS@@" + json.dumps(resultats, ensure_ascii=False))
"""


def _executer(
    programme: str, *, racine_du_code: Path, base: str, sortie_attendue: str
) -> tuple[str, subprocess.CompletedProcess[str]]:
    """Joue un programme avec le code d'un arbre donné, sur une base donnée."""
    environnement = dict(os.environ)
    environnement.update(ENVIRONNEMENT_COMMUN)
    environnement["METREO_DATABASE_URL"] = base
    environnement["METREO_STORAGE_ROOT"] = environnement["METREO_STORAGE_ROOT"]
    environnement["PYTHONPATH"] = os.pathsep.join(
        [
            str(racine_du_code / "apps" / "api" / "src"),
            str(racine_du_code / "packages" / "contracts" / "src"),
            str(racine_du_code / "packages" / "domain" / "src"),
        ]
    )
    resultat = subprocess.run(
        [sys.executable, "-c", programme],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(racine_du_code),
        env=environnement,
    )
    marque = f"@@{sortie_attendue}@@"
    for ligne in (resultat.stdout or "").splitlines():
        if ligne.startswith(marque):
            return ligne[len(marque) :], resultat
    return "", resultat


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--ancienne-revision", default=ANCIENNE_REVISION_PAR_DEFAUT)
    analyseur.add_argument(
        "--garder",
        action="store_true",
        help="ne pas effacer la base et le volume, pour les inspecter ensuite",
    )
    arguments = analyseur.parse_args()

    atelier = Path(tempfile.mkdtemp(prefix="metreo-retour-"))
    fichier = atelier / "retour.sqlite3"
    base = f"sqlite+pysqlite:///{fichier}"
    volume = atelier / "stockage"
    volume.mkdir()
    os.environ["METREO_STORAGE_ROOT"] = str(volume)

    try:
        print(f"Atelier : {atelier}")

        # -- 1. le schéma NEUF, amorcé ------------------------------------
        print("\n1. Montée du schéma neuf, et amorçage")
        environnement = dict(os.environ)
        environnement.update(ENVIRONNEMENT_COMMUN)
        environnement["METREO_DATABASE_URL"] = base
        environnement["PYTHONPATH"] = os.pathsep.join(
            [
                str(RACINE / "apps" / "api" / "src"),
                str(RACINE / "packages" / "contracts" / "src"),
                str(RACINE / "packages" / "domain" / "src"),
            ]
        )
        for commande in (
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            [sys.executable, "-m", "metreo_api.seed"],
        ):
            fait = subprocess.run(
                commande,
                cwd=str(RACINE / "apps" / "api"),
                env=environnement,
                capture_output=True,
                text=True,
                check=False,
            )
            if fait.returncode != 0:
                print(fait.stdout[-2000:])
                print(fait.stderr[-2000:], file=sys.stderr)
                return 1
        tete = subprocess.run(
            [sys.executable, "-m", "alembic", "current"],
            cwd=str(RACINE / "apps" / "api"),
            env=environnement,
            capture_output=True,
            text=True,
            check=False,
        )
        print(
            f"   tête appliquée : {tete.stdout.strip().splitlines()[-1] if tete.stdout.strip() else '?'}"
        )

        # -- 2. un parcours PDF complet, avec le code neuf ------------------
        print("\n2. Un plan déposé, analysé, calibré, mesuré et tranché")
        brut, resultat = _executer(
            PROGRAMME_NEUF.replace("SCRIPTS", str(RACINE / "scripts")),
            racine_du_code=RACINE,
            base=base,
            sortie_attendue="ETAT",
        )
        if not brut:
            print(resultat.stdout[-3000:])
            print(resultat.stderr[-3000:], file=sys.stderr)
            return 1
        etat = json.loads(brut)
        print(f"   mesure enregistrée : {etat['mesure_lisible']}")

        # -- 3. l'ancien code, sur la même base, SANS migration ------------
        print(f"\n3. Récupération de l'ancien code ({arguments.ancienne_revision})")
        ancien = atelier / "ancien"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(ancien), arguments.ancienne_revision],
            cwd=str(RACINE),
            check=True,
            capture_output=True,
            text=True,
        )
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ancien),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        print(f"   ancien code : {sha}")

        print("\n4. Les parcours du métier, joués par l'ancienne API — sans migration")
        etat["racine_ancienne"] = str(ancien)
        brut, resultat = _executer(
            PROGRAMME_ANCIEN.replace("@@JSON@@", json.dumps(etat)),
            racine_du_code=ancien,
            base=base,
            sortie_attendue="RESULTATS",
        )
        if not brut:
            print(resultat.stdout[-3000:])
            print(resultat.stderr[-4000:], file=sys.stderr)
            return 1
        resultats = json.loads(brut)

        print()
        print(f"{'parcours':34} {'état':8} détail")
        print("-" * 100)
        for ligne in resultats:
            print(f"{ligne['parcours']:34} {ligne['etat']:8} {ligne['detail']}")

        casses = [ligne for ligne in resultats if ligne["etat"] != "OK"]
        print()
        print(f"{len(resultats) - len(casses)} parcours sur {len(resultats)} fonctionnent.")
        return 0
    finally:
        subprocess.run(
            ["git", "worktree", "prune"], cwd=str(RACINE), capture_output=True, check=False
        )
        if arguments.garder:
            print(f"\nConservé : {atelier}")
        else:
            shutil.rmtree(atelier, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
