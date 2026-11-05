### 🔐 1. Onglet "Secrets" (Cliquez sur `New repository secret`)

Il n'y a qu'une seule valeur sensible à mettre ici :

|Name (Nom exact)|Secret (Valeur à mettre)|Rôle|
|---|---|---|
|`WEBHOOK_URL`|`https://discord.com/api/webhooks/123456...`|L'URL complète du Webhook copié depuis votre salon Discord.|

---

### ⚙️ 2. Onglet "Variables" (Cliquez sur `New repository variable`)

Voici la liste complète des **10 variables** utilisées par le script lorsqu'il tourne automatiquement toutes les 12h (`schedule`). Vous pouvez toutes les créer pour piloter votre script depuis l'interface GitHub sans jamais modifier le code :

|Name (Nom exact)|Exemple de Value (Valeur)|Description / Rôle|
|---|---|---|
|`ORIGIN_AIRPORTS`|`CDG,ORY,BRU`|Codes IATA des aéroports de départ séparés par une virgule (sans espace).|
|`DEST_AIRPORTS`|`PVG,SHA`|Codes IATA des aéroports d'arrivée séparés par une virgule.|
|`DEPARTURE_DATE`|`2026-10-15`|Date d'aller cible au format `YYYY-MM-DD`.|
|`RETURN_DATE`|`2026-10-29`|Date de retour cible au format `YYYY-MM-DD`.|
|`DELTA_DAYS`|`1`|Marge de jours autour des dates (`1` = cherche à ±1 jour, `2` = ±2 jours).|
|`MAX_STOPS`|`1`|Nombre max d'escales (`0` = vol direct uniquement, `1` = max 1 escale, laisser vide = peu importe).|
|`ALERT_MAX_PRICE`|`650`|Prix seuil (en chiffres uniquement). Si le billet est `<= 650`, l'alerte Discord passe au vert avec `@here`.|
|`ONLY_NOTIFY_UNDER_THRESHOLD`|`false`|Mettez `false` pour recevoir le Top 5 à chaque exécution, ou `true` pour n'être notifié sur Discord **que** si le prix passe sous `ALERT_MAX_PRICE`.|
|`CURRENCY`|`EUR`|Devise souhaitée pour les prix (`EUR`, `USD`, `GBP`...).|
|`ADULTS`|`1`|Nombre de passagers adultes (`1`, `2`, etc.).|

---

### 💡 Bon à savoir sur la priorité des variables

Dans le fichier `.github/workflows/flight_tracker.yml`, j'ai écrit chaque ligne sous la forme :  
`${{ inputs.origin_airports || vars.ORIGIN_AIRPORTS || 'CDG,ORY,BRU' }}`

Cela signifie que :

1. **Quand le robot tourne tout seul (la nuit / toutes les 12h)** : `inputs` est vide, donc il utilise automatiquement vos **Repository Variables** (`vars.*`).

2. **Quand vous lancez un test manuel (`Run workflow`)** : ce que vous tapez dans la fenêtre pop-up (`inputs.*`) prend le dessus le temps de ce test, sans écraser vos **Repository Variables** habituelles !