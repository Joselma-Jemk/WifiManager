# WifiManager : conception

Application Qt qui pilote le **hotspot mobile de Windows** d'un PC dont la carte Wi-Fi est aussi la source Internet
(ici : le partage de connexion d'un téléphone). Vérifié sur la machine cible : hotspot activé, 1 appareil sur 8 maximum,
passerelle ICS en `192.168.137.x`, carte Qualcomm Atheros QCA61x4A.

## Fonctions

| Fonction | Moyen | Droits |
|---|---|---|
| État, liste des appareils (nom, MAC, IP) | API WinRT `NetworkOperatorTetheringManager` + table ARP | utilisateur |
| Mot de passe en un clic, QR code | `ConfigureAccessPointAsync` (le mot de passe actuel n'est jamais lu) | utilisateur |
| Démarrer, arrêter, relancer si éteint | `StartTetheringAsync` / `StopTetheringAsync` | utilisateur |
| **Bloquer un appareil** | filtre WFP sur la couche de transfert IPv4 (`FWPM_LAYER_IPFORWARD_V4`) | **administrateur** |
| **Accès de 6 h, 12 h, 24 h** | une échéance enregistrée ; à l'échéance, le filtre de blocage est posé | administrateur |

## Contraintes établies

- Le hotspot Windows n'a **qu'un seul mot de passe** et aucun moyen de retirer un appareil : le blocage par appareil ne
  peut pas passer par l'API du hotspot. Il se fait en filtrant le trafic transféré par le partage de connexion.
- Un filtre WFP exige les droits administrateur : il vit dans un **petit service**, installé avec l'accord de
  l'utilisateur (une fenêtre UAC). L'interface, elle, tourne sans droits.
- Les accès temporaires exigent qu'un programme tourne en permanence : l'échéance est appliquée par le service, pas
  par la fenêtre, pour qu'elle tienne même si l'interface est fermée.
- Le blocage par adresse MAC est contournable par un téléphone qui change d'adresse : c'est une protection raisonnable,
  pas absolue. Le filtre vise l'IP attribuée (table ARP), et le service le met à jour quand l'IP d'un appareil change.
- Windows éteint le hotspot après quelques minutes sans appareil quand l'économie d'énergie est activée : c'est la cause
  probable des extinctions « toutes seules ». Le désactiver demande les droits administrateur.

## Architecture

```
Interface Qt (utilisateur)  ──canal local──►  Service WifiManager (administrateur)
   - état, appareils, mot de passe               - filtres WFP (blocage)
   - noms, historique, QR                        - échéances 6/12/24 h
   - demandes de blocage / d'accès               - relance du hotspot, surveillance
        │                                              │
        └────── wifimanager.hotspot (WinRT via PowerShell) ◄┘
```

- `wifimanager.hotspot` : lecture et écriture du hotspot. Les secrets passent par l'environnement, jamais par la ligne de
  commande de PowerShell.
- `wifimanager.neighbors` : MAC → IP depuis `arp -a`.
- `wifimanager.passwords` : génération lisible (alphabet sans caractères ambigus) et validation WPA2.

## État d'avancement

- [x] Lecture de l'état et des appareils (testée sur le vrai hotspot, lecture seule)
- [x] PowerShell persistant : 1re lecture ~7 s (démarrage), les suivantes 0,4 à 0,6 s
- [x] Fenêtre Qt : état, appareils nommables, mot de passe avec QR code, démarrer/arrêter, relance automatique
- [x] **Blocage par appareil** : filtres WFP, vérifié sur le vrai PC (l'appareil bloqué ne répond plus au ping, répond
      de nouveau au déblocage, et après l'arrêt du service)
- [x] **Accès temporaires 6/12/24 h** (et durée libre) : l'échéance est appliquée par le service, elle survit à un redémarrage
- [x] **Pass invité avec QR code** : le premier NOUVEL appareil qui se connecte le réclame ; mode strict (nouveaux appareils retenus)
- [x] **Données consommées** : total exact (compteurs de l'adaptateur) ; par appareil exact s'il est seul, estimé sinon
- [x] Service de blocage installable (tâche planifiée à l'ouverture de session, une seule fenêtre UAC) et désactivable
- [x] Mot de passe du hotspot conservé chiffré (DPAPI) pour réafficher le QR code
- [x] **Maintenir allumé** : relance le hotspot s'il s'éteint ET le service de blocage s'il s'arrête (limite d'essais, la tâche
      planifiée redémarre aussi le service en cas de plantage)
- [x] **Interface moderne** : cartes arrondies, interrupteurs, avatars, pastilles d'accès, thème clair ou sombre qui suit Windows
- [x] Test de bout en bout sur le vrai PC : service administrateur + client non élevé (blocage, accès temporaire, arrêt)
- [ ] Démarrer, arrêter, changer le mot de passe : testés avec un exécuteur factice, **pas encore sur le vrai hotspot**
- [ ] Mesure exacte des données par appareil quand plusieurs sont connectés (demande une capture de paquets)
- [ ] Icône de l'application, zone de notification
- [ ] Installateur

## Limites connues

- Le blocage vise l'adresse IP de l'appareil : un appareil qui change d'adresse est re-bloqué à la lecture suivante (5 s).
- Une adresse MAC aléatoire changeante (confidentialité des téléphones) peut faire passer un appareil pour un nouvel appareil.
- Un appareil bloqué reste connecté au Wi-Fi : il n'a simplement plus Internet. Seul un changement de mot de passe le déconnecte.
- Les données consommées pendant que le service est arrêté ne sont pas comptées.
- Si le service s'arrête, les blocages disparaissent (session dynamique) : choix de sûreté, jamais de blocage orphelin.
- L'extinction automatique de Windows (« Économie d'énergie » du point d'accès mobile) n'est pas désactivée par l'application :
  aucun réglage fiable n'a pu être vérifié. Le maintien allumé relance le hotspot quelle que soit la cause.
