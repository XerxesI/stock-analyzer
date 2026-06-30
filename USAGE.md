# 📊 Stock Analyzer - Kasutusjuhend v2.1 (FINAL)

Seniorlevel refaktooritud indeksite kataloog. **18 indeksit, 0 dubleeringud.**

---

## 🚀 Kiire start

```bash
# Üksikut aktsiat analüüsida
python main.py AAPL --period 6mo

# Konkreetse indeksi scanneerimine
python finder.py --market ai --top 15
python finder.py --market biotech_genomics --top 10

# KÕIKI indekseid korraga (deduplicate!)
python scan_all.py --confidence 0.5 --top 20
```

---

## 🗂️ INDEKSITE STRUKTUUR (18 indeksit - optimeeritud)

### 🟢 CORE - Baseline (3)
Stabiilsed, laiapõhjaised aktsiad.

| Indeks | Käsk | Sisaldab |
|--------|------|----------|
| **sp500** | `python finder.py --market sp500 --top 15` | AAPL, MSFT, NVDA, AMZN, META, GOOGL jne |
| **nasdaq** | `python finder.py --market nasdaq --top 15` | AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA jne |
| **europe** | `python finder.py --market europe --top 10` | SAP, ASML, SIE, BMW, AZN, EDF jne |

---

### 🔵 SECTOR - Valdkonnad (5)
Puhas, fokuseeritud valdkond-coverage.

| Indeks | Käsk | Sisaldab |
|--------|------|----------|
| **semiconductor** ✅ *Puhastatud* | `python finder.py --market semiconductor --top 10` | NVDA, AMD, INTC, ARM, AVGO, MU, ASML, AMAT, LRCX, KLAC, ON |
| **energy** | `python finder.py --market energy --top 10` | NEE, EXC, DUK, SO, AEP, XEL, D, WEC, EIX, AWK |
| **biotech_genomics** | `python finder.py --market biotech_genomics --top 10` | CRSP, EDIT, BEAM, DNA, NTLA, VRTX, REGN, BIIB, GILD, AMGN |
| **cloud_saas** | `python finder.py --market cloud_saas --top 10` | SNOW, CRM, DDOG, NET, OKTA, MDB, ZS, TEAM, PSTG, DBEX |
| **cybersecurity** | `python finder.py --market cybersecurity --top 10` | CRWD, PANW, ZS, FTNT, S, NET, OKTA, CHKP, ALRM, RNG |

---

### 🟣 THEMATIC - Tulevikud teemad (10)

| Indeks | Käsk | Sisaldab | Risk |
|--------|------|----------|------|
| **ai** | `python finder.py --market ai --top 15` | NVDA, AMD, MSFT, GOOGL, META, TSLA, AVGO, MSTR, PLTR, CRM, SNOW, UPST, SMCI, DELL, INTC, QCOM, ARM, SNPS, ANET, DDOG | HIGH |
| **ai_datacenters** | `python finder.py --market ai_datacenters --top 10` | SMCI, AVGO, EQIX, DLR, CCI, AMZN, MSFT, GOOGL, META, NVDA | HIGH |
| **emerging_tech** | `python finder.py --market emerging_tech --top 10` | PLTR, UPST, CRWD, NET, DDOG, OKTA, SMCI, EQIX, DLR, CCI | HIGH |
| **ev** 🚗 | `python finder.py --market ev --top 10` | TSLA, RIVN, LCID, NIO, XPEV, BYDDF, GM, F, VWAGY | HIGH |
| **energy_storage** 🔋 | `python finder.py --market energy_storage --top 10` | TSLA, ENPH, FLNC, STEM, QS, FREY, PLUG, BLNK, CHPT, EVGO | MEDIUM |
| **robotics** 🤖 | `python finder.py --market robotics --top 10` | ISRG, IRBT, ABB, FANUY, TER, ADSK, NNDM, KTOS, RBOT, UPSI | MEDIUM |
| **quantum** 💫 | `python finder.py --market quantum --top 10` | IONQ, RGTI, QBTS, GOOG, MSFT, IBM, INTC, AAPL, AMZN, NVDA | HIGH |
| **drones** ✈️ | `python finder.py --market drones --top 10` | ACHR, JOBY, AVAV, TXT, GD, NOC, LMT, RTX, IRDM, BA | HIGH |
| **nuclear_energy** ⚛️ | `python finder.py --market nuclear_energy --top 10` | EXC, NEE, DUK, SO, UEC, CCJ, PII, OKLO, LEU, URG | MEDIUM |
| **renewable_energy** 🌱 | `python finder.py --market renewable_energy --top 10` | NEE, PLUG, FSLR, ENPH, RUN, CWEN, AERI, RGEN, AQN, ICLN | MEDIUM |

---

### 🔴 EXPERIMENTAL - Risky (1)
Aasia aktsiad - Yahoo Finance piirangud.

| Indeks | Käsk | Märkus |
|--------|------|--------|
| **global_asia** | `python finder.py --market global_asia --top 10` | ⚠️ Segadused - ADR probleem, Yahoo piirangud |

---

## 🎯 ADVANCED KÄSUD

### Deduplicated skanneerimine (PARIM)
```bash
# Kõik indeksid, NO dubleeringud, deduplicate käivitatud
python scan_all.py --confidence 0.5 --top 20

# Väga tugevad signaalid
python scan_all.py --confidence 0.75 --top 15

# Kõik signaalid
python scan_all.py --confidence 0.3 --top 50
```

### Perioodiga
```bash
python finder.py --market ai --period 6mo --top 15
python finder.py --market semiconductor --period 3mo --top 10
python scan_all.py --confidence 0.5 --period 1y --top 25
```

---

## 🔍 VÄLJUNDI SELGITUS

**Signal:** STRONG BUY > BUY > HOLD > SELL > STRONG SELL

**Confidence:**
- 🟢 high (0.7-1.0) - väga usaldusväärne
- 🟡 medium (0.3-0.7) - mõõdukas
- 🔴 low (< 0.3) - nõrk
- `adjusted_confidence` = confidence, mida fundamentals kiht modifitseerib.

**Rank:** 0.0–1.0 skoor, kõrgem = parem
- Rank on nüüd **hybrid modulaator**: `technical_rank * (0.5 + 0.5 * fundamental_score)`
- Bias mõju rankile on nüüd **sujuv**: `(fundamental_score - 0.5) * 0.1`
- `fundamental_bias`: bullish / neutral / bearish (sekundaarne conviction-kiht)
- `fundamental_completeness`: kui palju fundamentals välju oli saadaval (0.0–1.0)

**Type:** trend_following | reversal | mixed_buy

---

## 📊 API (REST)

```bash
# API käivitamine
uvicorn api:app --reload

# Analyüsi
curl "http://localhost:8000/analyze?symbol=AAPL"

# TOP signaalid
curl "http://localhost:8000/opportunities?market=ai&limit=10"

# TOP signaalid + minimaalne fundamentals filter (optional)
curl "http://localhost:8000/opportunities?market=ai&limit=10&min_fundamental_score=0.4"

# Võrdlus
curl "http://localhost:8000/compare?symbols=NVDA,AMD,INTC"

# Runtime metrics
curl "http://localhost:8000/metrics"
```

---

## 🛠️ RUNBOOK (production hardening)

- **Parallelism:** globaalne concurrency cap + fetch throttle hoiavad nested parallelismi kontrolli all.
- **Caching:** `data_fetcher`, `market_context`, `fundamentals` (24h TTL) ja API vastused kasutavad TTL cache'i LRU evictioniga.
- **Metrics:** `/metrics` endpoint näitab request latency/error rate, cache hit-rate ja persistib loendurid faili `runtime_metrics.json` (või `STOCK_ANALYZER_METRICS_FILE`).

## ✅ SENIOR-LEVEL PARANDUSED (v2.1)

### Parandatud ✅
- **Semiconductor puhastatud** - ainult true chipid (NVDA, AMD, INTC, ARM, AVGO, MU, ASML, AMAT, LRCX, KLAC, ON)
- **Korea/Japan eemaldatud** - ühendatud global_asia experimental-iks
- **Deduplicate lisatud** - scan_all.py - NO dubleeringud (NVDA kordus 3x, nüüd 1x)
- **Overlapping aktsiaid arvutatud** - NVDA, AMD, INTC on mitmetes indeksis (intentionaalne)

### Eemaldatud ❌
- korea, japan, china (eraldi)

### Indeksite arv ✅
- Oli: 21
- Nüüd: 18
- Ideaalne: 12-14 (hetkel OK)

---

## ⚠️ DISCLAIMER

- **EI ole finantsaalane nõuanne**
- Ainult informatiivne analüüs
- Konsulteeri spetsialisti
- Turu risk alati olemas

---

**Versioon:** 2.1 FINAL
**Indeksite arv:** 18 (optimeeritud)
**Dubleeringud:** 0 (deduplicated)
**Status:** Production-ready ✅
