# Pantavisor System

Socle système et assembleur de plateformes Pantavisor. Version propre au projet :
**0.1.0**. Première cible matérielle : **Arduino VENTUNO Q**.

System construit son BSP/initramfs et consomme des composants externes :

```text
pantavisor-core ── composant Core ───────┐
                                       ├─ System → état privé → EFI/store
pantavisor-k0s ─── composant k0s ────────┤
                  BSP System ──────────┘
```

Il ne construit pas les images Core/k0s et n'importe pas leur code Python.
Chaque composant conserve sa version, son contrat et sa provenance. Les sources
des producteurs ne sont plus nécessaires une fois leurs exports obtenus.

## Structure

| Dossier | Responsabilité |
| --- | --- |
| `boards/ventuno-q/` | BSP public épinglé, sélection des modules/firmwares et paramètres du portage |
| `runtime/boot/` | Bootstrap Pantavisor, watchdog, stockage et services hôte |
| `recovery/` | Init du secours indépendant, distinct des services Core |
| `build/meta-system/` | Recettes et correctifs Yocto du système seulement |
| `kas/system.yml` | Construction de l'initramfs, sans targets Core/k0s |
| `compositions/` | Profils base/k0s et surcharges de déploiement |
| `devices/example.json` | Modèle des paramètres privés d'une carte |
| `tools/pvbuild/` | BSP, vérification des composants, assemblage, rendu et EFI |
| `tests/` | Contrats d'artefacts, compatibilité, cpio, firmware et boot |
| `docs/` | Interfaces et utilisation locale |

Le runtime hôte du premier portage conserve certains noms internes `ventuno-*`.
Ils appartiennent à System et ne sont pas des dépendances de Core ou de k0s.
Le laboratoire historique et ses identifiants de carte n'ont pas été importés.

## Parcours local

Prérequis de base : Linux, Python ≥3.11, Git, [Task](https://taskfile.dev/) v3 et Podman rootless.
`task --list` affiche les tâches disponibles. Sans argument, `task` lance `build`.
Le packaging BSP utilise GNU tar, `ar`, zstd et `depmod`.

```sh
task check
task build
python3 -m tools.pvbuild package-bsp --output dist/bsp
```

Les caches publics sont facultatifs : `DOWNLOAD_CACHE`, `SSTATE_CACHE` pour le
build et `VENDOR_CACHE` pour les paquets du BSP. Leurs chemins sont fournis par
l'utilisateur ; aucune ancienne arborescence de dépôt n'est requise.

Après construction et export dans les dépôts producteurs :

```sh
python3 -m tools.pvbuild assemble --bsp dist/bsp \
  --core ../pantavisor-core/dist/core --output dist/base-platform

python3 -m tools.pvbuild assemble --profile k0s --bsp dist/bsp \
  --core ../pantavisor-core/dist/core --k0s ../pantavisor-k0s/dist/base \
  --output dist/k0s-platform

python3 -m tools.pvbuild verify dist/k0s-platform
```

Ces chemins voisins sont seulement des exemples d'emplacement **des artefacts**.
Les deux profils consomment le même Core, incluant les clients Kubernetes ;
le profil k0s ajoute uniquement le serveur et son intégration.
Une copie dans n'importe quel autre répertoire convient. `--version` définit la
version System de l'assemblage, indépendamment des versions des composants.
`--override` surcharge explicitement les paramètres de composition.

Pour obtenir ultérieurement un composant depuis un registre, utiliser
`fetch-component --reference ghcr.io/...@sha256:... --output <répertoire>` avec
ORAS installé. Le même validateur contrôle les composants locaux et téléchargés.
Il attend l'artefact rootfs associé, pas un tar de couche d'image arbitraire.

## Rendu privé et boot natif

Créer `devices/ma-carte.local.json` depuis le modèle et fournir une clé publique
Ed25519. Les UUID et numéros de série doivent correspondre au matériel cible.

```sh
python3 -m tools.pvbuild render --bundle dist/k0s-platform \
  --device devices/ma-carte.local.json --output dist/device-state

python3 -m tools.pvbuild boot-tools --output artifacts/boot-tools

python3 -m tools.pvbuild native-package --state dist/device-state \
  --system-uuid <UUID-systeme> --esp-uuid <UUID-FAT> \
  --grub-modules artifacts/boot-tools/usr/lib/grub/arm64-efi \
  --output dist/installation
```

Le rendu et les EFI contiennent une clé d'hôte : **ce sont des sorties privées**,
exclues de Git et distinctes des composants publics. Ces commandes préparent des
fichiers ; elles n'écrivent pas une carte ni ses variables UEFI.

## Outillage et VM locale

Une image d'outillage fournit les dépendances de rendu et de qualification :

```sh
podman build -t localhost/pantavisor-system-tools:dev -f build/Containerfile.tools build
podman run --rm --security-opt label=disable -v "$PWD:/work" \
  localhost/pantavisor-system-tools:dev integration \
  --bundle dist/base-platform \
  --grub-modules artifacts/boot-tools/usr/lib/grub/arm64-efi \
  --output artifacts/integration-base
```

Ce harnais utilise uniquement des VM locales, des UUID fictifs, un serveur NTP
local et des clés éphémères. Il teste la base : boot, reboot, persistance, secours,
console, partition absente/illisible et coupure simulée. Le profil k0s est rendu
et vérifié localement ; sa qualification complète de cluster reste distincte.

Avec le Core unique incluant les clients Kubernetes, le scénario nominal a été
validé avec QEMU 10.2.2 sur le poste. L'essai dans l'image d'outillage QEMU 8.2
a rencontré un verrouillage noyau ; voir [les détails de validation](docs/validation.md).

## État et limites

Les trois projets repartent chacun en `0.1.0`. Ce nouveau contrat d'artefact et
les chemins de configuration des composants diffèrent de ceux du monorepo.
Ces versions ne remplacent pas implicitement les anciennes révisions d'une carte.

Le BSP reste épinglé au noyau 6.8.0-1084-qcom. La VENTUNO Q nécessite la chaîne
EFI reconnue par son firmware ; les mises à jour atomiques BSP/ESP et l'incident
de reset du portage précédent ne sont pas considérés résolus par le découpage.
La carte n'a pas été contactée pendant cette extraction. La CI et la publication
des trois dépôts seront traitées ensuite.

Voir [les contrats et frontières](docs/components.md) et
[la validation locale](docs/validation.md).
