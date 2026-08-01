# Alertes Leboncoin

Intégration Home Assistant qui surveille des recherches leboncoin et signale
une nouvelle annonce en moins de deux minutes.

Tout tourne dans Home Assistant, les recherches s'ajoutent et se modifient
depuis l'interface. L'intégration ne notifie pas elle-même : elle émet un
événement, et vous décidez dans votre propre automatisation comment vous
voulez être averti.

## Installation

Via HACS, en dépôt personnalisé (`Intégration`), puis **Ajouter une
intégration → Alertes Leboncoin**. Un redémarrage de Home Assistant est
nécessaire après le téléchargement.

L'icône est embarquée dans `custom_components/leboncoin_alert/brand/` et
servie par Home Assistant lui-même (2026.3+), sans passer par le CDN
`brands.home-assistant.io` qui ne connaît pas les intégrations personnalisées.
Le panneau de téléchargement de HACS continue lui d'interroger le CDN et
affichera un carré vide : c'est une limite de HACS, pas de l'intégration.

## Ajouter une recherche

Chaque recherche est une *sous-entrée* : depuis la page de l'intégration,
**Ajouter une recherche**. Le même bouton permet ensuite de les modifier ou
de les supprimer individuellement.

| Champ | Rôle |
|---|---|
| URL de recherche | Collée depuis la barre d'adresse, filtres déjà appliqués. Les paramètres de tracking sont retirés automatiquement. |
| Mots-clés exigés / exclus | Filtrage local. Virgule = alternatives, espace = tous les mots exigés. |
| Achat en cours | Écarte les annonces qu'un acheteur a déjà engagées. |
| États refusés | Écarte selon l'état déclaré par le vendeur (« Pour pièces » par défaut). |
| Intervalle | 90 s par défaut. |
| Plage de silence | Aucun sondage ni alerte ; les annonces trouvées sont regroupées dans un digest discret à la fin. |

La recherche est **exécutée une fois avant l'enregistrement** : le formulaire
refuse une URL invalide et vous prévient si vos mots-clés écartent tout.

Le premier sondage enregistre les annonces déjà en ligne **sans notifier**
(sinon vous recevriez 35 alertes d'un coup).

## Entités et événements

Chaque recherche crée un appareil avec un capteur `sensor.<recherche>_derniere_annonce` :
l'état est le titre de la dernière annonce retenue, les attributs portent le
prix, l'URL, l'image, la ville et le statut du sondage.

L'intégration émet un événement `leboncoin_alert_new_ads` à chaque lot
d'annonces retenues. C'est le seul point d'accroche pour être averti :

```yaml
triggers:
  - trigger: event
    event_type: leboncoin_alert_new_ads
actions:
  - action: telegram_bot.send_message
    data:
      message: "{{ trigger.event.data.top.title }} — {{ trigger.event.data.top.url }}"
```

Charge utile : `search`, `subentry_id`, `kind` (`live` ou `catchup`), `count`,
`ads[]`, `top`.

## L'intégration ne notifie personne

C'est délibéré. Elle constate que des annonces correspondent et émet
l'événement `leboncoin_alert_new_ads` — la façon dont vous voulez l'apprendre
(notification critique, Telegram, une lampe qui vire au rouge) vous appartient
et vit dans votre propre automatisation.

Une automatisation d'exemple est fournie dans `homeassistant/automation.yaml`.

Un point à connaître si vous partez sur Telegram : son API n'a aucune notion
de priorité, elle n'expose que `disable_notification`. Téléphone en silencieux,
alerte ratée. Seule la notification **critique** de l'app companion passe le
mode silencieux et le Ne pas déranger — et iOS exige une autorisation
explicite : Réglages → Home Assistant → Notifications → **Alertes critiques**.
Sans elle, l'option est ignorée sans le dire.

## Ne pas se faire bloquer

Leboncoin est derrière DataDome. Ce qui compte, dans l'ordre :

**1. La cohérence de l'identité.** Mesuré sur l'API réelle, 6 requêtes par
combinaison :

| Profil TLS | Warm-up sur www.leboncoin.fr | Résultat |
|---|---|---|
| `chrome_android` | oui | **6/6 OK** |
| `safari_ios` | non | **6/6 OK** |
| `safari_ios` | oui | 0/6 (captcha) |
| `chrome_android` | non | 0/6 (captcha) |

DataDome ne tire pas au sort : il vérifie que l'empreinte TLS est cohérente
avec le contexte de navigation. Une app native ne charge jamais le site web
d'abord, un navigateur le fait toujours. Les deux récits passent, le mélange
jamais. La librairie `lbc` envoie toujours le warm-up, d'où le choix de
`chrome_android` — c'est la cause n°1 des 403 « inexplicables ».

**2. L'empreinte TLS.** `requests` et `httpx` sont grillés au premier appel :
leur signature JA3 ne ressemble à aucun navigateur. `curl_cffi`, via `lbc`,
règle ça.

**3. L'IP.** Une IP résidentielle française passe sans proxy.

**4. La cadence.** 90 s avec jitter, et le sondage **s'arrête la nuit**. Une
recherche qui s'interrompt quand les humains dorment ressemble beaucoup plus à
un humain.

Un 403 isolé reste normal (~1 sondage sur 6 même en configuration saine).
L'intégration le traite comme un incident passager — nouvelle session, pause
courte — et ne bascule sur le backoff exponentiel (5 → 60 min) qu'après une
série. Réessayer immédiatement est précisément ce qui transforme un challenge
passager en blocage durable.

## Le filtrage local, non négociable

La recherche plein texte de leboncoin est large. Mesuré sur une vraie réponse
à `text=apple tv 4K` : **35 résultats, 9 pertinents**. Le reste : projecteurs
vidéo, clés HDMI, ampoules connectées, télécommandes Siri vendues seules,
supports muraux.

Sans filtre, ces faux positifs déclenchent une alerte — et quelques fausses
alertes suffisent à ce qu'on désactive tout le dispositif.

Deux pièges vérifiés :

- une exclusion trop large se retourne contre vous : `telecommande` écarte
  aussi « Apple TV a1842 64 Go **sans** télécommande », qui est une vraie
  annonce. D'où `siri remote` plutôt que `telecommande` ;
- `apple tv` laisse passer les modèles non-4K (3ᵉ génération). Pour ne cibler
  que la 4K : `apple tv 4k,appletv 4k`.

Les annonces écartées apparaissent dans les logs en `debug` avec leur motif.

### Deux contrôles qui ne devinent rien

Le titre d'une annonce ne dit pas tout. Deux attributs structurés le disent :

- **`transaction_status: pending`** — un acheteur s'est déjà engagé. Leboncoin
  l'affiche « Achat en cours ». Sur une recherche d'objets peu chers avec
  livraison, c'était 9 résultats sur 35 : ces annonces sont perdues d'avance.
- **`condition`** — l'état déclaré, parmi `pourpieces`, `etatsatisfaisant`,
  `bonetat`, `tresbonetat`, `reconditionne`, `etatneuf`, `neufavecetiquette`.
  Seul `pourpieces` est refusé par défaut.

Une annonce peut cumuler les deux tout en ayant un titre irréprochable —
« Apple TV 4k s'allume mais rien à l'écran » était à la fois `pourpieces` et
`pending`.

**Environ 30 annonces sur 100 n'ont aucun attribut `condition`.** Une annonce
sans état déclaré n'est donc jamais rejetée pour ce motif, sinon le filtre
supprimerait un tiers des résultats en silence.

## Attention aux recherches avec livraison

`shippable=1` rend le rayon géographique quasi inopérant : les résultats
remontent de toute la France. Cohérent avec « livraison possible », mais ce
n'est pas ce que suggère un filtre « 5 km autour de chez moi ». Par ailleurs
leboncoin encode deux rayons dans l'URL (`..._5000_50000`) et c'est le second,
étendu, que l'API applique.

## Version autonome

`standalone/` contient la version Docker d'origine : même logique, mêmes
protections, mais en conteneur séparé avec une automatisation Home Assistant
pour la diffusion. Elle ne sert plus que de repli — si `curl_cffi` refuse de
s'installer dans votre Home Assistant (architectures sans wheel), c'est la
solution.

## Cadre

Les CGU de leboncoin interdisent l'extraction automatisée. Pour un usage
personnel à faible volume, le risque concret est le blocage d'IP, pas le
contentieux — les sanctions CNIL récentes visent la collecte massive de
données personnelles à des fins commerciales. L'intégration ne conserve que
l'identifiant, le titre, le prix et l'URL, ne collecte aucune coordonnée de
vendeur et ne republie rien.
