# Abonnés : application mobile Flutter et gestion du temps

Idée : un **abonné** (invité ou client) installe une application Flutter sur son téléphone. Tu fixes son crédit de
temps dans WifiManager (par exemple 3 jours, soit 72 h). Lui peut **suspendre** son utilisation, la **reprendre**, et
suivre son abonnement.

> Statut : conception, rien n'est encore codé. Voir [DESIGN.md](DESIGN.md) pour l'état du reste du projet.

## Principe

```
Téléphone (Flutter) ──Wi-Fi du hotspot──►  Service sur le PC  ◄── app Qt (administrateur)
   voir / suspendre / reprendre              compte le temps,        crée les abonnés,
   demander une rallonge                     coupe ou rétablit       règle les durées,
                                             l'Internet (WFP)        répond aux demandes
```

1. Dans l'app Qt, tu crées « Marie, 72 h ». Un **QR code d'appairage** s'affiche.
2. Marie le scanne avec l'app Flutter : le téléphone mémorise un jeton secret et rejoint le Wi-Fi.
3. Le service compte le temps seulement quand le téléphone est **connecté et actif**. Il le reconnaît par son **jeton**,
   pas par son adresse MAC, qui peut changer sur les téléphones récents.
4. Marie touche « Suspendre » : le compteur s'arrête et son Internet est coupé. « Reprendre » le rallume.
5. À 0 h restante, l'accès se ferme tout seul. Marie peut demander une rallonge ; tu l'acceptes ou non dans l'app Qt.

## Ce que voit l'abonné (Flutter)

- Un anneau avec le **temps restant**.
- Un gros bouton **Suspendre / Reprendre**.
- Son statut : actif, suspendu, expiré.
- L'historique de ses sessions et les **données consommées**.
- Une alerte quand il reste peu de temps.
- Un bouton « Demander plus de temps ».

## Ce que tu gères (Qt)

Une section « Abonnés » :

- créer un abonné (nom, crédit d'heures, date de fin facultative) ;
- voir le temps restant, le statut et les données de chacun ;
- ajouter du temps, suspendre, révoquer ;
- régénérer le QR code d'appairage ;
- répondre aux demandes de rallonge.

## Modèle du temps

| | Accès temporaire (déjà fait) | Abonnement (à faire) |
|---|---|---|
| Ce qui compte | une échéance sur l'horloge (6 h, 12 h, 24 h) | un **crédit d'heures d'utilisation** |
| Le temps s'écoule | toujours, jusqu'à l'échéance | seulement quand l'appareil est connecté ET non suspendu |
| Suspension | non | oui, par l'abonné ou par toi |
| Fin | à l'échéance, puis blocage | quand le crédit tombe à 0, puis blocage |

« 3 jours » signifie donc 72 h d'usage. Une date de fin facultative peut s'ajouter en plus (« 72 h, valables jusqu'au
20/11 »).

## Points techniques importants

1. **Le blocage actuel empêcherait de reprendre.** Aujourd'hui, tous les paquets d'un appareil bloqué sont jetés, y compris
   ceux qui vont vers le PC. Un abonné suspendu ne pourrait plus joindre le service pour se réactiver. Le blocage doit
   couper **seulement l'accès à Internet** (couche de transfert du pare-feu), et laisser passer le dialogue avec le PC.
   À revérifier sur la machine : le test au ping ne suffira plus, il faudra un vrai téléphone.
2. **Ça ne marche que sur le Wi-Fi du hotspot.** Le téléphone parle au PC (`192.168.137.1`) à travers ce Wi-Fi. S'il en
   sort, il ne peut plus rien gérer. La gestion à distance demanderait un serveur en ligne : phase ultérieure.
3. **Identification par jeton.** Le téléphone envoie son jeton à intervalle régulier ; le service retrouve ainsi son
   adresse IP actuelle (et son MAC par la table ARP). L'abonnement suit l'appareil même si son MAC change.
4. **Sécurité.**
   - Le service écoute uniquement sur l'interface du hotspot, jamais sur le Wi-Fi qui alimente le PC.
   - Connexion chiffrée (TLS) ; l'empreinte du certificat est dans le QR code, que l'app Flutter vérifie.
   - Jetons révocables, limités en fréquence, qui ne permettent aucune action d'administrateur.
5. **Pas de dépendance au PC allumé** : si le PC est éteint, le temps ne s'écoule pas et rien n'est facturé à l'abonné.

## Application Flutter

- Emplacement prévu : un dossier `wifi_client` séparé, à côté de ce projet.
- Écrans : appairage (scan du QR code), tableau de bord (anneau du temps), historique, demande de rallonge.
- Android d'abord ; iOS plus tard.
- **Environnement** : Flutter 3.41.4 est installé. Android SDK et Java sont introuvables : l'app peut être écrite et
  testée, mais l'APK ne peut pas être compilé avant leur installation (téléchargements longs avec le réseau actuel).

## Étapes

1. **Service** : abonnés, compteur de temps, suspension et reprise, blocage limité à Internet, API chiffrée pour le téléphone.
2. **App Qt** : section « Abonnés » et QR code d'appairage.
3. **App Flutter** : appairage, tableau de bord, suspendre et reprendre.
4. **Demandes de rallonge** et notifications.
5. **Installateur** et publication.

## Décisions à confirmer

- [ ] **Temps** : crédit d'heures d'**utilisation** (recommandé), ou durée calendaire qui passe d'un coup, suspendu ou non ?
- [ ] **Date de fin** facultative en plus du crédit d'heures ?
- [ ] **Abonné** : un jeton lié à un téléphone, sans mot de passe (recommandé), ou de vrais comptes avec identifiant ?
- [ ] **Hors du Wi-Fi** : accepter que la gestion ne marche que sur le hotspot (recommandé pour commencer), ou prévoir un
  serveur en ligne ?
- [ ] **Plateformes** : Android seulement au départ ?
