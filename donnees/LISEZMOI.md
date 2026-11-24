# Données collectées chaque jour

Le script `collecte/collecte.py` tourne une fois par jour, vers 1 h 23 UTC (2 h 23 en hiver à Genève). Il ajoute une ligne ou un fichier par pool à chaque passage. Les six pools sont `eth` (ETH / USDC), `wbtceth` (WBTC / ETH), `wbtc` (WBTC / USDC), `hype` (HYPE / USDC), `sol` (SOL / USDC) et `qqq` (QQQ / USDG).

Tous les prix sont exprimés dans le sens étudié sur le site : l'actif dans la monnaie de cotation (par exemple des USDC pour 1 ETH, des ETH pour 1 WBTC).

## `bougies/<pool>.csv` : une ligne par jour terminé (UTC)

| Colonne | Contenu |
|---|---|
| `date` | jour (UTC) |
| `open`, `high`, `low`, `close` | prix d'ouverture, plus haut, plus bas et clôture de la pool ce jour-là |
| `volume_usd` | volume échangé dans la pool ce jour-là, en dollars |

Source : GeckoTerminal. Le premier passage (24 novembre 2026) a repris les 180 jours disponibles ; ensuite, chaque passage met à jour les 10 derniers jours. Ce sont les données brutes : les mèches aberrantes (WBTC / USDC en août et septembre, lancement de QQQ / USDG) ne sont pas corrigées ici, mais seulement dans les données du site.

## `releves/<pool>.csv` : un relevé par jour, à l'heure du passage

| Colonne | Contenu |
|---|---|
| `horodatage_utc` | moment du relevé |
| `prix` | prix calculé à partir du tick actuel de la pool : \(1{,}0001^{\text{tick}}\), corrigé des décimales des jetons |
| `tick` | tick actuel de la pool |
| `L` | liquidité active \(L\) : la somme des \(L\) de toutes les positions dont la plage contient le prix actuel (lue dans le contrat de la pool) |
| `tvl_usd` | valeur totale déposée dans la pool (TVL), en dollars (GeckoTerminal) |
| `volume_24h_usd` | volume des 24 dernières heures, en dollars (GeckoTerminal) |
| `prix_geckoterminal` | prix affiché par GeckoTerminal au même moment, pour vérification |

\(L\) est exprimé dans les unités brutes du contrat (sans les décimales des jetons), comme dans Uniswap v3.

## `liquidite/<pool>/<date>.json` : répartition de la liquidité

La liquidité active \(L\) par intervalle de prix, entre le prix actuel divisé par 1,5 et le prix actuel multiplié par 1,5. Chaque segment `[tick_bas, tick_haut, prix_bas, prix_haut, L]` donne la liquidité disponible si le prix se trouve dans cet intervalle.

Pour les pools Uniswap v3, ProjectX et QQQ, la répartition est reconstruite à partir des ticks initialisés du contrat (`tickBitmap` et `liquidityNet`). Pour SOL / USDC, elle vient de l'API de Raydium. Dans tous les cas, le segment qui contient le prix actuel a exactement la valeur de `L` du relevé.
