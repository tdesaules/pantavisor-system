# Qualification VENTUNO Q et diagnostic du reset

La carte est indisponible lors de cette implémentation. Aucun résultat matériel
ni correctif de reset n'est donc déclaré. Les essais suivants constituent la
procédure à exécuter dès son retour.

## Collecte

Depuis le poste, avec la clé et le fichier known_hosts de la carte :

```sh
python3 -m tools.pvbuild diagnostics --host root@ADRESSE --port 2222 \
  --ssh-key CHEMIN_CLE --known-hosts CHEMIN_KNOWN_HOSTS \
  --samples 360 --interval 10 --output artifacts/hardware-observation
```

Le collecteur n'écrit pas sur la carte, ne redémarre pas et continue pendant les
pertes SSH. Chaque échantillon horodaté contient boot ID, uptime, état du guard,
pression mémoire, températures, watchdog, pstore lisible, réseau et dmesg.
Les erreurs de permission restent dans la capture ; une collecte via Core ne
donne pas nécessairement accès à tous les diagnostics de l'hôte.

Capturer aussi la console série **depuis avant la mise sous tension**. Conserver
les sorties dans `artifacts/`, privées. Après un incident, relever le motif de
reset fourni par le firmware et les éventuels fichiers pstore depuis le secours.
Ne pas effacer pstore avant la collecte. L'absence de pstore n'exclut pas un reset
électrique ou watchdog.

## Matrice d'essais

Pour chaque ligne : enregistrer version/digest de la plateforme, firmware,
alimentation, périphériques, températures, boot IDs et logs série/SSH.

| Essai | Critère de succès |
| --- | --- |
| 10 démarrages à froid, base | SSH par clé, DHCP et heure synchronisée à chaque démarrage |
| 10 reboots commandés, base | boot ID changé, compteur Core incrémenté, données conservées |
| Secours dédié | console et SSH accessibles sans démarrer Core ni monter le store |
| Fonctionnement base pendant 1 h | aucun reset spontané, pas d'erreur mémoire/stockage |
| Même essai avec k0s | API et nœud Ready, DNS, trafic et volume persistants fonctionnels |
| Charge réseau, puis stockage, puis CPU séparément | absence de reset ; corréler chaque charge aux logs |
| Coupure pendant boot d'essai | secours accessible au boot suivant |

Commencer par base sans workloads, puis ajouter k0s et une charge à la fois.
Comparer les derniers événements série avec les traces persistantes du guard :
un reset avant le noyau, un panic, un arrêt watchdog et une perte SSH seule sont
des incidents différents. Ne pas attribuer la cause au découpage des dépôts sans
reproduction. La qualification des mises à jour BSP/ESP suit le protocole séparé
dans [updates.md](updates.md).
