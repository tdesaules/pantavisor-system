# Automatisation et publication

Ce document décrit le parcours initial. La chaîne à six jobs, les runners
hébergés et l'activation après publication du catalogue sont décrits dans
[la migration CI](ci-migration.md).

## Contrôles rapides

Les trois dépôts exécutent leurs tests à chaque push/PR sur Ubuntu 24.04.
System vérifie aussi les compositions, la syntaxe shell/Python et les liens de
documentation. Ces contrôles ne construisent pas Yocto.

## Builds froids

Les workflows manuels `release.yml` (producteurs) et `qualify.yml` (System)
utilisent un runner `[self-hosted, linux, x64, pantavisor]` : Linux, Python ≥3.11,
Git, Podman rootless, SquashFS, QEMU utilisateur, tar/ar/zstd, depmod et GRUB.
Prévoir au moins 16 Gio de RAM et 150 Gio libres ; les builds peuvent être longs.

Chaque exécution utilise un checkout neuf identifié par run ID et tentative,
refuse un répertoire `artifacts` préexistant et supprime les variables de caches
externes. Les caches téléchargements/sstate commencent donc vides. L'image KAS
peut être déjà présente dans le stockage Podman : « froid » désigne ici Yocto.
Le temps de build et les empreintes de sorties sont dans `build-provenance.json`.
Un administrateur doit nettoyer les anciens checkouts sur le runner après avoir
conservé les rapports nécessaires.

## Provenance

Le wrapper capture commit, état dirty et empreinte des sources avant KAS. Il ne
produit une attestation qu'après succès, sources inchangées et calcul des SHA-256
des sorties. Un échec invalide l'attestation précédente. Le packaging refuse une
sortie altérée ou des sources modifiées depuis le build, y compris les templates
runtime. Les anciens exports doivent être reconstruits avec ce wrapper.

## Qualification

System reçoit Core et k0s par références ORAS `ghcr.io/...@sha256:...`, assemble
les deux profils, construit l'outillage et exécute les scénarios VM. L'outillage
compile **QEMU 10.2.2** depuis une archive épinglée par SHA-256. Le harnais refuse
les autres versions et sauvegarde l'environnement de chaque exécution.

Le scénario `normal` k0s utilise 8 Gio de RAM et un disque applicatif ext4 de
12 Gio (sparse). Il vérifie API/nœud Ready, PVC, DNS, trafic HTTP inter-pods,
reboot et conservation d'un token unique. L'image BusyBox est épinglée par digest.
Un accès réseau aux registres Kubernetes est nécessaire. Les rapports JSON et
console sont publiés en artefacts CI ; clés SSH, disques et états privés ne le sont
pas. Les scénarios de panne du socle sont également exécutés pour le profil k0s.

## Publication

Lancer le workflow manuel depuis le tag `v<VERSION>` avec `publish=true`.
Les producteurs publient une image OCI `ghcr.io/<owner>/<repo>:<VERSION>` et un
artefact ORAS `ghcr.io/<owner>/<repo>/component:<VERSION>`. System publie son BSP
sous `ghcr.io/<owner>/<repo>/bsp:<VERSION>` après qualification des deux profils.
Les scripts affichent la référence par digest à consommer. Les images OCI ne
sont pas interchangeables avec les artefacts de composants.

La publication exige une provenance de build propre et une intégrité vérifiée.
Configurer le runner et les droits GHCR avant le premier lancement. Les workflows
sont implémentés mais leur exécution GitHub et une publication effective restent
à réaliser après push des changements.
