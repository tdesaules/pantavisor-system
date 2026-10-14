# Frontières et contrat des artefacts

## Propriété

- **Core** possède ses services, clients Kubernetes inclus, recettes, image et contrat.
- **k0s** possède son serveur, sa configuration Kubernetes et son contrat.
- **System** possède le BSP, les volumes physiques, les budgets, la composition,
  la récupération et le rendu des paramètres privés.

System consomme des exports, jamais les sources ou modules Python des deux autres
projets. Les petits exporteurs de chaque producteur utilisent les outils natifs
et le même format déclaré ; aucun framework de build central ni quatrième dépôt
n'est requis. Chaque producteur peut être construit depuis un checkout isolé.

## `component.json`, schéma 2

Un composant rootfs fournit au minimum :

```text
component.json
runtime.json
lxc.conf.in
rootfs.squashfs
rootfs.tar.gz
packages.manifest
sbom.spdx.tar.zst
```

Le descripteur déclare nom, version propre, architecture, ABI, capacités,
provenance et inventaire des fichiers avec tailles et SHA-256. Son propre digest
est calculé sur les octets de `component.json`. Il ne dépend pas de l'emplacement
du répertoire. Core fournit aussi `core-release.json`.

Les ABI initiales sont `pantavisor-core-v1` et `pantavisor-k0s-v1`. Une image OCI
contient le même userspace ; l'artefact de fichiers fournit en plus les informations
nécessaires à l'assemblage Pantavisor/LXC. Le digest d'un manifeste OCI et celui
de `component.json` désignent des objets différents.

Le BSP utilise aussi le schéma 2, avec `kind: bsp`, son contrat de boot et son
inventaire : noyau, initramfs, archive vendor sélectionnée, notices et SBOM.

## Composition et versions

System vérifie les empreintes, refuse les fichiers supplémentaires et les liens
symboliques dans les exports, puis contrôle architecture, ABI et capacités.
Les deux profils utilisent le même composant `core`, qui doit déclarer
`kubernetes-clients`. Seul le profil k0s ajoute le serveur Kubernetes.

Il n'exige pas l'égalité des versions ou commits. Une plateforme System 0.2.0
peut utiliser un Core 0.8.1 et une intégration k0s 0.3.0 si leurs contrats restent
compatibles. La version amont de k0s est encore un autre champ.

Le `bundle.json` assemblé contient les composants, leurs digests, versions et
provenances originales. Il porte la version System. Les chemins des entrées
locales ne sont pas enregistrés comme identité des composants.

## Contrat runtime

Les templates LXC sont livrés par les producteurs. System y injecte les budgets,
les sources de montages et les paramètres de la carte. Les services reçoivent :

- Core : `/etc/pantavisor/network.conf`, `/etc/pantavisor/time.conf`, `/data`,
  l'API de gestion et le contrôle hôte sur `/run/platform` ;
- k0s : `/etc/pantavisor/k0s.env`, les volumes persistants et le signal d'heure
  sur `/clock-status/time-ready` ;
- Core peut lire `/k0s-access/admin.conf` sans posséder les
  données internes du serveur.

Le secours System conserve son propre réseau et ses binaires dans l'initramfs.
Il n'a pas besoin de démarrer Core ou k0s. Les identifiants de disques et détails
de l'eMMC/NVMe appartiennent exclusivement à System et aux paramètres privés.

## Publication ultérieure

Les images OCI sont construites localement par les producteurs. La définition
des workflows, droits de publication, tags et catalogues distants est reportée
à l'étape CI. Aucun tag ou digest de publication fictif n'est utilisé comme pin.
Les dépendances amont existantes restent verrouillées par leurs vrais commits
et checksums. Les métadonnées `source.dirty` signalent les builds locaux non committés.
