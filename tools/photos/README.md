# Retouche des photos d'annonce (mobile.de)

Uniformise un lot de photos de véhicule : même fond, même colorimétrie,
même cadrage 4:3 — ce que mobile.de attend pour une galerie propre.

## Installation

```bash
pip install pillow numpy scipy "rembg[cpu]" onnxruntime
```

Le modèle de détourage (`isnet-general-use`, ~179 Mo) se télécharge tout seul
au premier lancement, dans `~/.rembg/models/`.

## Utilisation

```bash
# Traitement du lot, mode décidé photo par photo
python3 tools/photos/retouch.py photos/ -o photos_retouchees/

# Fond studio imposé (vues extérieures uniquement)
python3 tools/photos/retouch.py photos/exterieur/ -o out/ --mode studio

# Décor conservé mais atténué
python3 tools/photos/retouch.py photos/ -o out/ --mode harmonise

# Correction colorimétrique et cadrage seuls, sans découpe
python3 tools/photos/retouch.py photos/interieur/ -o out/ --mode correction
```

## Les trois traitements

| Mode | Effet | Pour quelles photos |
|---|---|---|
| `studio` | Véhicule détouré, posé sur un fond gris clair uni avec ombre portée | Vues extérieures (3/4 avant, profil, arrière) |
| `harmonise` | Décor conservé mais flouté et désaturé, véhicule net | Extérieurs dont le décor doit rester lisible |
| `correction` | Balance des blancs, niveaux, ombres, netteté, cadrage 4:3 | Intérieur, compteur, moteur, coffre, clés |

En `--mode auto` (défaut), chaque photo est classée d'après la surface et la
position du masque de détourage : un sujet compact posé au sol part en
`studio`, tout le reste en `correction`.

## Sorties

JPEG 1600x1200 (4:3), qualité 92, numérotés dans l'ordre de traitement —
l'ordre des fichiers est celui de la galerie de l'annonce.

## Limites connues

- Le détourage automatique peut mordre sur les vitres teintées et les jantes
  ajourées d'une carrosserie sombre sur fond sombre. Vérifier chaque sortie en
  mode `studio` avant publication.
- Aucun floutage de plaque d'immatriculation n'est appliqué automatiquement.
