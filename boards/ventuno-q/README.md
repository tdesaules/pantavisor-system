# Portage VENTUNO Q

`board.json` décrit les points de raccordement du runtime et l'architecture.
`platform.lock.json` verrouille les paquets publics du noyau 6.8.0-1084-qcom et
des firmwares. Le noyau n'est pas compilé par Yocto dans ce portage : il est
extrait de ces paquets et vérifié par empreinte. Yocto construit l'initramfs
Pantavisor et ses outils.

Les politiques de `bsp/` sélectionnent les modules/firmwares utiles, sans Wi-Fi.
`boot-tools.lock.json` identifie les modules GRUB ARM64 utilisés pour préparer
les EFI. `bsp/recovery-downloads.json` conserve les références publiques de la
restauration constructeur ; aucun outil de flash automatique n'est lancé.

Les identités eMMC/NVMe, la MAC et les clés SSH sont des entrées privées du rendu.
Elles ne sont pas des constantes de ce profil public. Le bootstrap runtime
hérité de ce portage vise p71 et le NVMe configuré ; un autre matériel nécessite
son propre adaptateur et une qualification distincte.
