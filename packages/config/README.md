# packages/config — réservé

**Vide volontairement.** Les configurations partagées (ruff, mypy) vivent dans
le `pyproject.toml` de la racine ; celles du web vivent dans `apps/web/`.

Ce répertoire accueillera les préréglages partagés (ESLint, TypeScript, Tailwind
ou équivalent) quand un second paquet frontend existera. Avec une seule
application web, une couche d'indirection n'apporterait rien.
