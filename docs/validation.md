# Validation locale du découpage

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
