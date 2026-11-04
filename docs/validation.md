# Validation locale du découpage

## Automatisation du 16 octobre 2026

- Builds KAS System/Core/k0s réussis avec les caches locaux existants ; durées
  enregistrées : environ 37 s, 28 s et 30 s. Ce ne sont pas des builds froids.
- Exports avec provenance de build, intégrité et capacités runtime vérifiées.
- Validation des trois descripteurs contre `schemas/component.schema.json`.
- Smoke ARM64 dans l'outillage : Bash, kubectl, k0sctl, k9s et k0s réussis.
- Images OCI ARM64 Core/k0s construites localement sous le tag `qualification`.
- Image d'outillage construite avec QEMU 10.2.2 compilé depuis l'archive épinglée.
- Les sept scénarios base ont réussi **dans cette image d'outillage** :
  `artifacts/qualification-base-20261016/results/`.
- Les six scénarios de panne/secours du profil k0s ont réussi :
  `artifacts/qualification-k0s-20261016-v2/results-faults/`.
- Le scénario fonctionnel k0s a également réussi : API et nœud Ready, PVC,
  DNS et HTTP inter-pods, reboot, conservation du token écrit avant reboot,
  retour au secours et vérification ext4 du système/store. Preuves :
  `artifacts/qualification-k0s-20261016-v2/results/`.
  Le premier essai avait révélé les sous-répertoires applicatifs manquants dans
  la fixture de disque ; le harnais les crée maintenant avant le démarrage.
- Contrôles locaux : 29 tests System, 10 tests Core et 8 tests k0s réussis.
- Workflows des trois dépôts contrôlés par actionlint 1.7.7.

Les nouveaux exports sont `dist/qualification-bsp-20261016/` côté System et
`dist/qualification-20261016/` côté producteurs. Leur provenance indique
`dirty: true` : ce sont des artefacts de développement, non publiables.

La validation matérielle et le build froid distant restent à exécuter. Le
prototype A/B est testé hors ligne et n'active pas une mise à jour sur le boot v1.
Voir [automation.md](automation.md), [hardware-qualification.md](hardware-qualification.md)
et [updates.md](updates.md).

Les sections ci-dessous décrivent les essais historiques précédant ces ajouts.

## Vérifié

- Builds KAS séparés : initramfs System, Core et k0s.
- Exports publics avec inventaires, SBOM et contrats runtime.
- Images OCI ARM64 locales pour Core et k0s.
- Exécution QEMU utilisateur : Bash 5.3.20, kubectl 1.36.4, k0sctl 0.33.1,
  k9s 0.51.0 et k0s v1.36.4+k0s.1.
- Assemblage local des profils base et k0s à partir des seuls exports.
- Tests de versions et commits indépendants, ABI/architecture incompatibles,
  altération de fichiers, bundles privés refusés et invariants de boot.
- VM base UEFI : système absent/illisible, store absent, environnement invalide,
  boot normal, reboot, secours, console série, coupure simulée et secours dédié.
- Synchronisation de l'heure depuis une RTC virtuelle à 1970 et publication du
  signal `time-ready` par Core, via un serveur NTP local de test.

Les builds ont réutilisé explicitement des caches publics de téléchargements et
sstate. Cela n'est pas une mesure de build froid. Les sources des composants
sont celles des nouveaux dépôts ; aucune donnée d'accès à la carte n'a été copiée.

## Corrections révélées par les essais

- Le contrôle du prochain boot passe désormais par `/run/platform/next` côté Core.
- Le DHCP du secours n'écrit plus dans le volume `/data` appartenant à Core.
- La VM utilise la même MAC dans le runtime et le secours, pour conserver le bail
  DHCP derrière les redirections SLIRP après reboot.
- Le réseau virtio de test n'utilise pas de ROM PXE.

## À qualifier séparément

- Cluster k0s complet avec les nouveaux chemins/configurations.
- Installation et essais matériels après extraction ; la carte est indisponible.
- Diagnostic du reset spontané du portage historique.
- Mises à jour atomiques du BSP et de l'ESP.
- CI et publication de chacun des trois projets.

Les preuves VM locales sont sous `artifacts/integration-split-base-v4/` et
`artifacts/integration-final-clock/`. Le rendu k0s avec noms et DNS personnalisés
est dans `dist/k0s-render-test/`.
Le contrôle nominal final de la nouvelle arborescence et du signal d'heure est
dans `artifacts/integration-final-layout/`, et le rendu courant dans
`dist/k0s-render-current/`.
Les artefacts et états privés restent ignorés par Git.

## Core unique avec clients Kubernetes inclus

Core produit désormais un seul export `dist/core/`, avec kubectl, k0sctl et k9s
obligatoires. Les profils System base et k0s consomment exactement ce même
composant ; seul le second ajoute le serveur k0s.

Le build, le packaging, l'image OCI et les versions des quatre binaires ARM64
(Bash compris) ont été vérifiés. Les deux assemblages sont dans
`dist/base-unified-core/` et `dist/k0s-unified-core/`.

Le scénario nominal, avec NTP, reboot, retour au secours et vérification ext4,
a réussi sur le QEMU 10.2.2 du poste :
`artifacts/integration-unified-core/results-host/`. Le premier essai du même
paquet dans l'image d'outillage QEMU 8.2 a rencontré un soft lockup noyau et un
timeout au deuxième boot. Cette différence d'environnement reste à diagnostiquer ;
elle ne constitue pas une validation de ce scénario dans l'image QEMU 8.2.
