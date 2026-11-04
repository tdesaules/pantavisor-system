# Mise à jour BSP/ESP : protocole A/B et limite du boot v1

## Pourquoi une nouvelle ABI de boot est nécessaire

`emmc-file-store-v1` utilise des chemins fixes pour le noyau, l'initramfs, le store
et les fichiers GRUB. L'EFI embarque aussi le secours et l'identifiant
d'installation. Remplacer deux fichiers séparément ne constitue pas une mise à
jour atomique. Remplacer le store par une image neuve détruirait les données Core.

Le module `tools/pvbuild/update_protocol.py` implémente un **prototype hors ligne**
de transaction, testé avec coupures injectées. Il n'est pas relié au boot v1 et
n'est volontairement pas exposé comme commande de mise à jour de carte.

## Protocole retenu pour une future ABI v2

1. Conserver le store et les volumes applicatifs hors des générations BSP.
2. Écrire les EFI dans un nouveau répertoire immuable de l'ESP ; fsync des
   fichiers et répertoires. Ne jamais écraser le secours connu fonctionnel.
3. Écrire noyau/initramfs/configuration dans une génération ext4 inactive,
   avec manifeste d'intégrité, puis fsync.
4. Vérifier les deux ensembles avant de remplacer atomiquement un unique
   sélecteur sur ext4. Garder la génération précédente.
5. Démarrer la nouvelle génération en essai avec un budget persistant. La
   confirmation exige Core Ready, heure synchronisée et, pour le profil k0s,
   API/nœud Ready. Une panne doit revenir à la génération précédente ou au secours.
6. Ne collecter les anciennes générations qu'après confirmation et conservation
   d'au moins une génération de repli.

Le prototype couvre les étapes de staging, sélection et rollback. Les tests
interrompent l'exécution après ESP, système, manifeste, avant et après sélecteur,
et refusent un EFI altéré. Il vérifie les transitions logiques ; un test Python
sur répertoires ne démontre pas la durabilité du firmware/FAT après coupure réelle.

## Intégration restant à qualifier

- Dispatcher EFI stable reconnu par le firmware, capable de lire le sélecteur v2.
- Adaptation de GRUB, du bootstrap et du guard aux chemins générationnels.
- Verrouillage exclusif et contrôle UUID/identité des montages dans l'updater réel.
- Migration explicite v1 → v2 sans remplacer le store ni les données applicatives.
- Budget d'essai/confirmation et rollback automatique sur échec de santé.
- Coupure QEMU puis carte à chaque frontière d'écriture, notamment sur FAT.

**La mise à jour atomique de la carte reste non qualifiée.** Le contrat v1 reste
inchangé jusqu'à validation complète de cette chaîne.
