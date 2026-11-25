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
| `frais_ppm` | taux de frais de la pool, en millionièmes (500 = 0,05 %) |
| `fee_growth_0`, `fee_growth_1` | compteurs de frais de la pool (`feeGrowthGlobal`) : frais cumulés depuis la création de la pool, par unité de liquidité, en jeton 0 et en jeton 1 (relevés depuis le 25 novembre 2026) |
| `format_fee_growth` | format des compteurs : `Q128` (pools de type Uniswap v3) ou `Q64` (Raydium) |

**Calculer les frais d'une journée.** La différence entre deux relevés, divisée par \(2^{128}\) (ou \(2^{64}\) pour Raydium), donne les frais gagnés entre les deux relevés par unité de \(L\), en unités brutes du jeton (sans les décimales). Une position de liquidité \(L_{\text{pos}}\) restée dans la plage pendant tout l'intervalle a gagné \(L_{\text{pos}} \times \Delta\text{fee\_growth} / 2^{128}\) de chaque jeton.

\(L\) est exprimé dans les unités brutes du contrat (sans les décimales des jetons), comme dans Uniswap v3.

## `bougies_horaires/<pool>.csv` : une ligne par heure terminée (UTC)

Mêmes colonnes que les bougies journalières, avec `heure_utc` à la place de `date`. Le premier passage (25 novembre 2026) a repris les 1 000 dernières heures disponibles (environ 41 jours) ; chaque passage met ensuite à jour les 48 dernières heures.

## `gas.csv` : coût estimé d'un réajustement, un relevé par réseau et par jour

| Colonne | Contenu |
|---|---|
| `reseau` | `ethereum` (pools ETH, WBTC), `hyperevm` (HYPE), `robinhood` (QQQ), `solana` (SOL) |
| `prix_gas` | prix du gas au moment du relevé (en gwei ; pour Solana, frais de priorité médians en micro-lamports par unité de calcul) |
| `prix_jeton_usd` | prix du jeton qui paie le gas (ETH, HYPE ou SOL), tiré des pools |
| `unites_estimees` | quantité de gas supposée pour un réajustement |
| `cout_reajustement_usd` | coût estimé d'un réajustement, en dollars |

Un réajustement comprend : retirer la position, récupérer les frais, rééquilibrer les deux jetons par un échange et ouvrir une nouvelle position. On l'estime à environ 700 000 unités de gas sur les pools de type Uniswap v3, et à 3 transactions d'environ 300 000 unités de calcul sur Solana (5 000 lamports par signature plus les frais de priorité). C'est un ordre de grandeur : le coût réel dépend de l'outil utilisé. Sur Robinhood Chain (réseau de couche 2), la petite part payée à Ethereum pour publier les données n'est pas comptée.

## `dvol.csv` : DVOL de Deribit, une ligne par jour

Clôture journalière (UTC) du DVOL, la volatilité implicite à 30 jours annualisée, en % : `dvol_btc` pour le bitcoin et `dvol_eth` pour l'ether. Le premier passage (25 novembre 2026) a repris l'historique depuis le 1er novembre 2023 ; chaque passage met ensuite à jour les derniers jours.

## `liquidite/<pool>/<date>.json` : répartition de la liquidité

La liquidité active \(L\) par intervalle de prix, entre le prix actuel divisé par 1,5 et le prix actuel multiplié par 1,5. Chaque segment `[tick_bas, tick_haut, prix_bas, prix_haut, L]` donne la liquidité disponible si le prix se trouve dans cet intervalle.

Pour les pools Uniswap v3, ProjectX et QQQ, la répartition est reconstruite à partir des ticks initialisés du contrat (`tickBitmap` et `liquidityNet`). Pour SOL / USDC, elle vient de l'API de Raydium. Dans tous les cas, le segment qui contient le prix actuel a exactement la valeur de `L` du relevé.
