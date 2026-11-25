#!/usr/bin/env python3
"""Collecte quotidienne des données des six pools du TM (volatilité, volume, TVL, liquidité).

Lancé une fois par jour. Pour chaque pool, le script :
  1. met à jour les bougies journalières (prix O/H/L/C et volume en USD) des derniers jours  -> donnees/bougies/<id>.csv
  2. ajoute un relevé instantané : prix, tick, L actif, TVL, volume 24 h                      -> donnees/releves/<id>.csv
  3. enregistre la répartition de la liquidité autour du prix (de ÷1,5 à ×1,5)                -> donnees/liquidite/<id>/<date>.json
  4. met à jour les bougies horaires des dernières heures                                   -> donnees/bougies_horaires/<id>.csv
  5. relève le compteur des frais de la pool (feeGrowthGlobal) dans le relevé instantané
  6. estime le coût d'un réajustement de position (gas) sur chaque réseau                    -> donnees/gas.csv
  7. met à jour le DVOL de Deribit (volatilité implicite à 30 jours du BTC et de l'ETH)       -> donnees/dvol.csv

Sources : GeckoTerminal (bougies, volume, TVL), le contrat de la pool lu directement sur sa blockchain
(prix, tick, L, répartition), et l'API de Raydium pour la répartition de la pool Solana.
Uniquement la bibliothèque standard de Python.
"""
import json, os, sys, time, math, csv, base64, struct, datetime as dt, urllib.request

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DON = os.path.join(RACINE, "donnees")
GT = "https://api.geckoterminal.com/api/v2"
ETH_RPC = ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com", "https://cloudflare-eth.com"]
FENETRE = math.log(1.5)            # répartition de la liquidité : prix entre P/1,5 et P×1,5
UA = {"User-Agent": "Mozilla/5.0 (TM volatilite)", "content-type": "application/json"}

# actif_token0 : True si le prix étudié (actif en monnaie de cotation) est token1 par token0
POOLS = [
    dict(id="eth", nom="ETH / USDC", reseau="eth", adresse="0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640", type="evm", rpc=ETH_RPC,
         dec0=6, dec1=18, actif_token0=False, cote_gt="base"),
    dict(id="wbtceth", nom="WBTC / ETH", reseau="eth", adresse="0x4585fe77225b41b697c938b018e2ac67ac5a20c0", type="evm", rpc=ETH_RPC,
         dec0=8, dec1=18, actif_token0=True, cote_gt="base"),
    dict(id="wbtc", nom="WBTC / USDC", reseau="eth", adresse="0x99ac8ca7087fa4a2a1fb6357269965a2014abc35", type="evm", rpc=ETH_RPC,
         dec0=8, dec1=6, actif_token0=True, cote_gt="base"),
    dict(id="hype", nom="HYPE / USDC", reseau="hyperevm", adresse="0x6c9a33e3b592c0d65b3ba59355d5be0d38259285", type="evm",
         rpc=["https://rpc.hyperliquid.xyz/evm"], dec0=18, dec1=6, actif_token0=True, cote_gt="quote", lot=8, pause=1.2),
    dict(id="sol", nom="SOL / USDC", reseau="solana", adresse="3ucNos4NbumPLZNWztqGHNFFgkHeRMBQAVemeeomsUxv", type="raydium",
         dec0=9, dec1=6, actif_token0=True, cote_gt="base"),
    dict(id="qqq", nom="QQQ / USDG", reseau="robinhood", adresse="0xd60a5d14db690b7afad71f76b108071d7175597d", type="evm",
         rpc=["https://rpc.mainnet.chain.robinhood.com"], dec0=6, dec1=18, actif_token0=False, cote_gt="base", lot=10, pause=1.5),
]

def log(*a): print(*a, flush=True)

def http_json(url, data=None, essais=5, attente=8):
    for i in range(essais):
        try:
            req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as e:
            log("   nouvel essai", i + 1, url[:60], e)
            time.sleep(attente * (i + 1))
    raise RuntimeError("échec : " + url)

# ---------- appels aux contrats (JSON-RPC, par lots) ----------
def eth_calls(pool, appels):
    """appels = liste de (adresse, données) ; renvoie la liste des résultats hexadécimaux dans le même ordre."""
    lot, pause = pool.get("lot", 40), pool.get("pause", 0.3)
    out = [None] * len(appels)
    for debut in range(0, len(appels), lot):
        part = appels[debut:debut + lot]
        corps = [{"jsonrpc": "2.0", "id": debut + i, "method": "eth_call", "params": [{"to": a, "data": d}, "latest"]} for i, (a, d) in enumerate(part)]
        for essai in range(6):
            url = pool["rpc"][essai % len(pool["rpc"])]
            try:
                rep = http_json(url, corps, essais=1)
                if isinstance(rep, dict): rep = [rep]
                if any("result" not in x for x in rep): raise RuntimeError(str([x.get("error") for x in rep if "result" not in x][:1]))
                for x in rep: out[x["id"]] = x["result"]
                break
            except Exception as e:
                log("   RPC", url, e); time.sleep(3 * (essai + 1))
        else:
            raise RuntimeError("RPC indisponible pour " + pool["id"])
        time.sleep(pause)
    return out

def i_signe(h, bits):
    v = int(h, 16) & ((1 << bits) - 1)
    return v - (1 << bits) if v >> (bits - 1) else v

def enc_int(v):
    return format(v & ((1 << 256) - 1), "064x")

def prix_tick(pool, tick):
    p = 1.0001 ** tick * 10 ** (pool["dec0"] - pool["dec1"])     # token1 par token0
    return p if pool["actif_token0"] else 1 / p

def etat_evm(pool):
    a = pool["adresse"]
    slot0, liq, esp, fg0, fg1, frais = eth_calls(pool, [(a, "0x3850c7bd"), (a, "0x1a686502"), (a, "0xd0c93a7c"),
                                                        (a, "0xf3058399"), (a, "0x46141319"), (a, "0xddca3f43")])
    corps = slot0[2:]
    tick = i_signe(corps[64:128], 24)
    # feeGrowthGlobal0X128 / 1X128 : frais cumulés par unité de liquidité depuis la création de la pool (format Q128)
    return dict(tick=tick, L=int(liq, 16), espacement=int(esp, 16), fg0=int(fg0, 16), fg1=int(fg1, 16), q=128,
                frais_ppm=int(frais, 16))

def repartition_evm(pool, etat):
    """Liquidité active par intervalle de ticks, entre P/1,5 et P×1,5, reconstruite à partir des ticks initialisés."""
    a, t, s = pool["adresse"], etat["tick"], etat["espacement"]
    w = int(FENETRE / math.log(1.0001)) + s
    c_bas, c_haut = (t - w) // s, (t + w) // s
    mots = list(range(c_bas >> 8, (c_haut >> 8) + 1))
    cartes = eth_calls(pool, [(a, "0x5339c296" + enc_int(m)) for m in mots])          # tickBitmap(int16)
    ticks = []
    for m, carte in zip(mots, cartes):
        b = int(carte, 16)
        for bit in range(256):
            if b >> bit & 1:
                c = m * 256 + bit
                if c_bas <= c <= c_haut: ticks.append(c * s)
    ticks.sort()
    infos = eth_calls(pool, [(a, "0xf30dba93" + enc_int(k)) for k in ticks])           # ticks(int24)
    net = {k: i_signe(r[2:][64:128], 128) for k, r in zip(ticks, infos)}
    return segments(pool, t, etat["L"], net, t - w, t + w)

def segments(pool, t, L, net, bas, haut):
    """Découpe [bas, haut] en intervalles de ticks de liquidité constante, à partir de liquidityNet."""
    segs = []
    dessus = sorted(k for k in net if k > t)
    dessous = sorted((k for k in net if k <= t), reverse=True)
    l, g = L, t
    for k in dessus:                       # vers le haut : en franchissant k, L += liquidityNet(k)
        segs.append((g, k, l)); l += net[k]; g = k
    segs.append((g, haut, l))
    l, d = L, t
    for k in dessous:                      # vers le bas : on traverse k dans l'autre sens, L -= liquidityNet(k)
        segs.append((k, d, l)); l -= net[k]; d = k
    segs.append((bas, d, l))
    out = []
    for a, b, l in sorted(segs):
        if b <= a: continue
        pa, pb = prix_tick(pool, a), prix_tick(pool, b)
        out.append([a, b, round(min(pa, pb), 8), round(max(pa, pb), 8), str(max(l, 0))])
    return out

# ---------- pool Raydium (Solana) ----------
def etat_raydium(pool):
    rep = http_json("https://api.mainnet-beta.solana.com", {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                    "params": [pool["adresse"], {"encoding": "base64"}]})
    b = base64.b64decode(rep["result"]["value"]["data"][0])
    # PoolState Raydium CLMM : tick_spacing u16 @235, liquidity u128 @237, sqrt_price_x64 u128 @253, tick_current i32 @269
    # fee_growth_global_0_x64 u128 @277, fee_growth_global_1_x64 u128 @293 (format Q64) ; taux de frais lu dans l'amm_config
    cfg = http_json("https://api.mainnet-beta.solana.com", {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                    "params": [base58(b[9:41]), {"encoding": "base64"}]})
    c = base64.b64decode(cfg["result"]["value"]["data"][0])
    return dict(espacement=struct.unpack_from("<H", b, 235)[0], L=int.from_bytes(b[237:253], "little"),
                tick=struct.unpack_from("<i", b, 269)[0], fg0=int.from_bytes(b[277:293], "little"),
                fg1=int.from_bytes(b[293:309], "little"), q=64, frais_ppm=struct.unpack_from("<I", c, 47)[0])

def base58(octets):
    alpha = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(octets, "big"); out = ""
    while n: n, r = divmod(n, 58); out = alpha[r] + out
    return "1" * (len(octets) - len(octets.lstrip(b"\0"))) + out

def repartition_raydium(pool, etat):
    rep = http_json("https://api-v3.raydium.io/pools/line/position?id=" + pool["adresse"])
    t = etat["tick"]; w = int(FENETRE / math.log(1.0001))
    lignes = sorted((int(x["tick"]), int(x["liquidity"])) for x in rep["data"]["line"])
    out = []
    for (k, l), suiv in zip(lignes, lignes[1:] + [(t + w, 0)]):
        a, b = max(k, t - w), min(suiv[0], t + w)
        if b <= a: continue
        out.append([a, b, round(prix_tick(pool, a), 8), round(prix_tick(pool, b), 8), str(l)])
    return out

# ---------- GeckoTerminal : bougies, volume, TVL ----------
def gecko(pool):
    # premier passage : tout l'historique disponible (180 jours) ; ensuite, les 10 derniers jours suffisent
    lim = 10 if os.path.exists(os.path.join(DON, "bougies", pool["id"] + ".csv")) else 1000
    base = f"{GT}/networks/{pool['reseau']}/pools/{pool['adresse']}"
    info = http_json(base)["data"]["attributes"]; time.sleep(2.5)
    ohlc = http_json(f"{base}/ohlcv/day?aggregate=1&limit={lim}&currency=token&token={pool['cote_gt']}")["data"]["attributes"]["ohlcv_list"]; time.sleep(2.5)
    vol = http_json(f"{base}/ohlcv/day?aggregate=1&limit={lim}&currency=usd&token={pool['cote_gt']}")["data"]["attributes"]["ohlcv_list"]; time.sleep(2.5)
    vmap = {r[0]: r[5] for r in vol}
    limh = 48 if os.path.exists(os.path.join(DON, "bougies_horaires", pool["id"] + ".csv")) else 1000
    oh = http_json(f"{base}/ohlcv/hour?aggregate=1&limit={limh}&currency=token&token={pool['cote_gt']}")["data"]["attributes"]["ohlcv_list"]; time.sleep(2.5)
    vh = http_json(f"{base}/ohlcv/hour?aggregate=1&limit={limh}&currency=usd&token={pool['cote_gt']}")["data"]["attributes"]["ohlcv_list"]; time.sleep(2.5)
    vhm = {r[0]: r[5] for r in vh}
    return info, [(r[0], r[1], r[2], r[3], r[4], vmap.get(r[0])) for r in ohlc], [(r[0], r[1], r[2], r[3], r[4], vhm.get(r[0])) for r in oh]

def maj_horaires(pid, lignes, maintenant):
    chemin = os.path.join(DON, "bougies_horaires", pid + ".csv"); os.makedirs(os.path.dirname(chemin), exist_ok=True)
    data = {}
    if os.path.exists(chemin):
        with open(chemin, newline="") as f:
            for r in csv.DictReader(f): data[r["heure_utc"]] = r
    for ts, o, h, l, c, v in lignes:
        if ts + 3600 > maintenant.timestamp(): continue          # l'heure en cours n'est pas terminée
        k = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:00Z")
        data[k] = dict(heure_utc=k, open=f"{o:.8g}", high=f"{h:.8g}", low=f"{l:.8g}", close=f"{c:.8g}", volume_usd=f"{v:.2f}" if v is not None else "")
    with open(chemin, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["heure_utc", "open", "high", "low", "close", "volume_usd"]); w.writeheader()
        for k in sorted(data): w.writerow(data[k])

# ---------- coût d'un réajustement (gas) ----------
# Estimation : retirer la position, récupérer les frais, rééquilibrer les deux jetons par un échange et ouvrir une nouvelle
# position (nouveau NFT) ≈ 700 000 unités de gas sur une pool de type Uniswap v3 ; sur Solana, ≈ 3 transactions.
GAS_REAJUSTEMENT = 700_000
RESEAUX = [("ethereum", "https://ethereum-rpc.publicnode.com", "ETH", "eth"),
           ("hyperevm", "https://rpc.hyperliquid.xyz/evm", "HYPE", "hype"),
           ("robinhood", "https://rpc.mainnet.chain.robinhood.com", "ETH", "eth")]

def releve_gas(maintenant, prix):
    lignes = []
    for nom, url, jeton, ref in RESEAUX:
        try:
            g = int(http_json(url, {"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice", "params": []})["result"], 16)
            cout = g * GAS_REAJUSTEMENT / 1e18 * prix[ref]
            lignes.append(dict(reseau=nom, prix_gas=f"{g/1e9:.6g}", unite="gwei", jeton=jeton, prix_jeton_usd=f"{prix[ref]:.6g}",
                               unites_estimees=GAS_REAJUSTEMENT, cout_reajustement_usd=f"{cout:.4f}"))
        except Exception as e: log("   gas", nom, e)
        time.sleep(1)
    try:
        pf = http_json("https://api.mainnet-beta.solana.com", {"jsonrpc": "2.0", "id": 1, "method": "getRecentPrioritizationFees",
                       "params": [["CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"]]})["result"]
        v = sorted(x["prioritizationFee"] for x in pf); med = v[len(v) // 2] if v else 0     # micro-lamports par unité de calcul
        lamports = 3 * (5000 + med * 300_000 / 1e6)                                            # 3 transactions, ≈ 300 000 unités de calcul
        lignes.append(dict(reseau="solana", prix_gas=f"{med:.6g}", unite="micro-lamports/CU (priorité, médiane)", jeton="SOL",
                           prix_jeton_usd=f"{prix['sol']:.6g}", unites_estimees="3 transactions", cout_reajustement_usd=f"{lamports/1e9*prix['sol']:.4f}"))
    except Exception as e: log("   gas solana", e)
    chemin = os.path.join(DON, "gas.csv"); champs = ["horodatage_utc", "reseau", "prix_gas", "unite", "jeton", "prix_jeton_usd", "unites_estimees", "cout_reajustement_usd"]
    jour = maintenant.strftime("%Y-%m-%d"); anciennes = []
    if os.path.exists(chemin):
        with open(chemin, newline="") as f: anciennes = [r for r in csv.DictReader(f) if r["horodatage_utc"][:10] != jour]
    for l in lignes: l["horodatage_utc"] = maintenant.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(chemin, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=champs); w.writeheader(); w.writerows(anciennes + lignes)
    return lignes

# ---------- écriture ----------
def maj_bougies(pid, lignes, aujourdhui):
    chemin = os.path.join(DON, "bougies", pid + ".csv"); os.makedirs(os.path.dirname(chemin), exist_ok=True)
    data = {}
    if os.path.exists(chemin):
        with open(chemin, newline="") as f:
            for r in csv.DictReader(f): data[r["date"]] = r
    for ts, o, h, l, c, v in lignes:
        d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
        if d >= aujourdhui: continue        # la bougie du jour n'est pas terminée
        data[d] = dict(date=d, open=f"{o:.8g}", high=f"{h:.8g}", low=f"{l:.8g}", close=f"{c:.8g}", volume_usd=f"{v:.2f}" if v is not None else "")
    with open(chemin, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume_usd"]); w.writeheader()
        for d in sorted(data): w.writerow(data[d])

def ajout_releve(pid, ligne):
    chemin = os.path.join(DON, "releves", pid + ".csv"); os.makedirs(os.path.dirname(chemin), exist_ok=True)
    champs = ["horodatage_utc", "prix", "tick", "L", "tvl_usd", "volume_24h_usd", "prix_geckoterminal", "frais_ppm", "fee_growth_0", "fee_growth_1", "format_fee_growth"]
    lignes = []
    if os.path.exists(chemin):
        with open(chemin, newline="") as f: lignes = [r for r in csv.DictReader(f) if r["horodatage_utc"][:10] != ligne["horodatage_utc"][:10]]
    lignes.append(ligne)
    with open(chemin, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=champs, restval=""); w.writeheader(); w.writerows(sorted(lignes, key=lambda r: r["horodatage_utc"]))

# ---------- DVOL de Deribit ----------
def maj_dvol(maintenant):
    chemin = os.path.join(DON, "dvol.csv"); data = {}
    premier = not os.path.exists(chemin)
    if not premier:
        with open(chemin, newline="") as f:
            for r in csv.DictReader(f): data[r["date"]] = r
    fin = int(maintenant.timestamp() * 1000)
    debut = int(dt.datetime(2023, 11, 1, tzinfo=dt.timezone.utc).timestamp() * 1000) if premier else fin - 12 * 86400000
    jour = maintenant.date().isoformat()
    for cur in ("BTC", "ETH"):
        s = debut
        while s < fin:
            e = min(fin, s + 500 * 86400000)
            rep = http_json(f"https://www.deribit.com/api/v2/public/get_volatility_index_data?currency={cur}&start_timestamp={s}&end_timestamp={e}&resolution=1D")
            for t, o, h, l, c in rep["result"]["data"]:
                d = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).date().isoformat()
                if d >= jour: continue            # la journée en cours n'est pas terminée
                data.setdefault(d, {"date": d})[f"dvol_{cur.lower()}"] = f"{c:.2f}"
            s = e; time.sleep(1)
    with open(chemin, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "dvol_btc", "dvol_eth"], restval=""); w.writeheader()
        for d in sorted(data): w.writerow(data[d])
    return data[max(data)]

def main():
    maintenant = dt.datetime.now(dt.timezone.utc)
    jour = maintenant.date().isoformat()
    bilan = []; prix_ref = {}
    for pool in POOLS:
        pid = pool["id"]; log("==", pool["nom"])
        try:
            info, bougies, horaires = gecko(pool)
            maj_bougies(pid, bougies, jour)
            maj_horaires(pid, horaires, maintenant)
            etat = etat_raydium(pool) if pool["type"] == "raydium" else etat_evm(pool)
            prix = prix_tick(pool, etat["tick"]); prix_ref[pid] = prix
            segs = repartition_raydium(pool, etat) if pool["type"] == "raydium" else repartition_evm(pool, etat)
            px_gt = float(info["base_token_price_quote_token"] if pool["cote_gt"] == "base" else info["quote_token_price_base_token"])
            ajout_releve(pid, dict(horodatage_utc=maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"), prix=f"{prix:.8g}", tick=etat["tick"], L=etat["L"],
                                   tvl_usd=f"{float(info['reserve_in_usd']):.2f}", volume_24h_usd=f"{float(info['volume_usd']['h24']):.2f}",
                                   prix_geckoterminal=f"{px_gt:.8g}", frais_ppm=etat["frais_ppm"], fee_growth_0=etat["fg0"],
                                   fee_growth_1=etat["fg1"], format_fee_growth=f"Q{etat['q']}"))
            dossier = os.path.join(DON, "liquidite", pid); os.makedirs(dossier, exist_ok=True)
            with open(os.path.join(dossier, jour + ".json"), "w") as f:
                json.dump(dict(pool=pool["nom"], adresse=pool["adresse"], horodatage_utc=maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               prix=prix, tick=etat["tick"], L=str(etat["L"]), espacement_ticks=etat["espacement"],
                               colonnes=["tick_bas", "tick_haut", "prix_bas", "prix_haut", "L"], segments=segs), f, separators=(",", ":"))
            ecart = abs(math.log(prix / px_gt))
            bilan.append(f"{pool['nom']} : prix {prix:.6g} (écart avec GeckoTerminal {ecart*100:.2f} %), L {etat['L']}, {len(segs)} segments")
            if ecart > 0.02: bilan.append(f"  ATTENTION : écart de prix supérieur à 2 % pour {pool['nom']}")
        except Exception as e:
            bilan.append(f"{pool['nom']} : ÉCHEC ({e})")
    if all(k in prix_ref for k in ("eth", "hype", "sol")):
        for l in releve_gas(maintenant, prix_ref): bilan.append(f"gas {l['reseau']} : {l['prix_gas']} {l['unite']} -> réajustement ≈ {l['cout_reajustement_usd']} $")
    else: bilan.append("gas : non relevé (prix ETH, HYPE ou SOL manquant)")
    try:
        d = maj_dvol(maintenant); bilan.append(f"DVOL du {d['date']} : BTC {d.get('dvol_btc')} · ETH {d.get('dvol_eth')}")
    except Exception as e: bilan.append(f"DVOL : ÉCHEC ({e})")
    log("\n".join(bilan))
    return 0 if not any("ÉCHEC" in b for b in bilan) else 1

if __name__ == "__main__":
    sys.exit(main())
