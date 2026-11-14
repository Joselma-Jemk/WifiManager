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
- [x] Fenêtre Qt : état, appareils nommables, changement de mot de passe avec QR code, démarrer/arrêter
- [x] Surveillance et relance automatique (désactivée par défaut, jamais contre un arrêt fait dans l'application)
- [x] Démarrer, arrêter, changer le mot de passe : testés avec un exécuteur factice, **pas encore sur le vrai hotspot**
- [ ] Service administrateur et blocage WFP (à prototyper avec un appareil désigné par l'utilisateur)
- [ ] Accès temporaires 6/12/24 h
- [ ] Icône, zone de notification, notification à l'arrivée d'un nouvel appareil
- [ ] Mot de passe actuel conservé dans Vault pour pouvoir réafficher le QR code
- [ ] Installateur
