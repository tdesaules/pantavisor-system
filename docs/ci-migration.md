# Migration CI System

System est activé après le catalogue, Core et k0s. Il faut deux références de
composants ORAS réellement publiées par digest, pas leurs images OCI exécutables.

Activation préparée le 9 novembre 2026 : catalogue `1.6.1`, composants Core et
k0s issus des releases `v0.1.2`. Les digests résolus sont conservés dans
`compositions/components.json`. Actionlint, YAML et les tests Python locaux ont
réussi ; la qualification hébergée des profils `base` et `k0s` reste à exécuter.

Depuis le checkout `github-actions`, avec Python, `gh` et ORAS :

```sh
python3 scripts/pantavisor_consumers.py --project system \
  --catalogue-tag "$CATALOGUE_TAG" --directory ../pantavisor-system \
  --core-reference "$CORE_COMPONENT" --k0s-reference "$K0S_COMPONENT"
```

L'outil vérifie la release du catalogue et ses images, puis les références des
composants. Il génère `main.yml`, archive les anciens workflows et écrit les
références dans `compositions/components.json`. Relire, committer et pousser.
Le workflow final utilise des tags d'actions explicites ; les images d'actions
sont épinglées par SHA-256 dans le catalogue.

## Six jobs

1. `check` : changements, contrats, références et éventuel commit de release.
2. `lint` : contrôles spécialisés, sans commandes métier sur le runner.
3. `build` : KAS/BSP sur `ubuntu-24.04`, téléchargement des composants immuables,
   vérification des signatures des producteurs, assemblage base et k0s.
4. `test` : matrice statique base/k0s sur `ubuntu-24.04-arm`, `fail-fast: false`,
   QEMU 10.2.2 épinglé, contrôles fonctionnels et rapports publics séparés.
5. `release` : réunion des reçus base/k0s pour les mêmes fichiers, finalisation
   atomique commit/tag, puis publication du BSP signé et de la GitHub Release.
6. `maintenance` : Renovate, inventaire/rescan du BSP publié et liens.

Le scan du BSP complète son SBOM Yocto et son inventaire fournisseur ; il ne
garantit pas que chaque firmware ou module noyau est reconnu par le scanner.
Les rapports doivent être examinés avec ces métadonnées. Les composants Core/k0s
ont leurs propres scans de producteurs.

## Versions et preuves

Le développement se fait sur branches courtes, fusionnées vers `main`. Les push
directs sur main restent possibles. Une préparation manuelle sur main crée un
commit avec VERSION/changelog avant le build. Tous les jobs importent ce commit
exact ; une évolution concurrente de main empêche sa finalisation.

Le tag annoté `v<VERSION>` contient le run de préparation et le digest du reçu
combiné. Le workflow du tag vérifie ce run, récupère ses artefacts qualifiés et
publie sans reconstruire. La GitHub Release conserve les références Core/k0s,
les digests des bundles qualifiés et celui du BSP. Une panne de publication peut
être reprise sans déplacer le tag ni écraser une version. Les artefacts de
qualification sont conservés 30 jours ; leur expiration empêche une reprise
non prouvée. Les sorties privées de VM ne sont jamais publiées.

## Configuration et surveillance

Installer l'App partagée sur le dépôt, configurer la variable `BOT_APP_ID` et
le secret `BOT_APP_PRIVATE_KEY`. Le workflow généré utilise aussi cette App pour
Renovate, avec les droits d'écriture sur le contenu et les pull requests, sans
`RENOVATE_TOKEN` séparé. Éviter une seconde instance hébergée. Pour des packages privés, donner au token
du workflow System l'accès en lecture aux packages Core/k0s.

Surveiller l'espace libre et la durée du build, la vérification des digests et
signatures, puis les deux instances du job test. Un seul profil réussi ne permet
pas de release. Les rapports incluent aussi les échecs de préparation du harnais.
Les scans au seuil `high` et les signatures doivent réussir avant publication
de la GitHub Release. Les commits KAS, versions/checksums des recettes et BSP
fournisseur nécessitent encore des mises à jour coordonnées et qualifiées.

La qualification VM ne remplace pas les essais sur VENTUNO Q. Cette migration
n'effectue aucune installation sur carte et ne qualifie pas la mise à jour A/B.
