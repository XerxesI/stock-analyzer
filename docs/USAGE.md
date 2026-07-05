# 📊 Stock Analyzer - Kasutusjuhend v2.1 (FINAL)

Seniorlevel refaktooritud indeksite kataloog. **18 indeksit, 0 dubleeringud.**

---

## 🚀 Kiire start

> Käivita need käsud alati repo juurkaustast.

```powershell
# Üksikut aktsiat analüüsida
python -m stock_analyzer.cli.main AAPL --period 6mo
python -m stock_analyzer.cli.main DUK --mode defensive

# Konkreetse indeksi scanneerimine
python -m stock_analyzer.cli.finder --market ai --top 15
python -m stock_analyzer.cli.finder --market biotech_genomics --top 10
python -m stock_analyzer.cli.finder --market energy --mode defensive --top 10
python -m stock_analyzer.cli.finder --market ai --mode growth --min-growth-score 0.55 --max-risk-score 0.70 --top 10

# KÕIKI indekseid korraga (deduplicate!)
python -m stock_analyzer.cli.scan_all --confidence 0.5 --top 20
python -m stock_analyzer.cli.scan_all --mode auto --confidence 0.5 --top 20
python -m stock_analyzer.cli.scan_all --mode auto --confidence 0.5 --top 20 --portfolio
python -m stock_analyzer.cli.scan_all --mode auto --confidence 0.5 --top 20 --backtest --start-date 2025-01-01 --end-date 2025-12-31 --rebalance-days 7
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

## 🧪 HYBRID STRATEEGIA (core-satellite + momentum)

Uus, eraldiseisev strateegia-moodul (`run_hybrid.py`) — **ei kasuta vana signaali/ranki**
(mille ennustusvõime osutus mürarikkaks, IC≈0.02). Struktuur:
**CORE (SPY 70%) + SATELLITE (top-10 ristlõikeline 12-1 momentum, 30%)**,
kuine tasakaalustus, tehingukulud arvestatud, range no-lookahead.

```bash
# Walk-forward test 5 aknas + ablatsioon (core-only / +satelliit / +overlay) + SPY võrdlus
python run_hybrid.py

# Lisaks tehingukulude tundlikkus (0.00 / 0.10 / 0.25% per side)
python run_hybrid.py --costs

# No-lookahead kontroll (kiire sanity-check)
python run_hybrid.py --selftest

# Parameetrid (tunable)
python run_hybrid.py --core QQQ --core-weight 0.7 --sat-n 10
python run_hybrid.py --core SPY,QQQ --core-weight 0.6
```

**Moodulid:** `factors.py` (12-1 momentum + ristlõikeline rank), `hybrid_portfolio.py`
(core-satellite kaalud), `hybrid_backtest.py` (walk-forward mootor). Universum:
`LIQUID_LARGECAP` (~200 likviidset large/mid-cap nime) failis `universes.py`.

⚠️ **Ausalt (backtesti tulemus):** momentum-satelliit lõi SPY-d tootluselt kõigis
5 aknas, kuid SMA200-overlay kahjustas (whipsaw). **Suurim hoiatus:** `LIQUID_LARGECAP`
on tänaste nimede snapshot → **survivorship bias** võimendab momentumit. Eelis pole
tõestatud, kuni pole testitud survivorship-vaba universumiga. Vt ka ⚠️ DISCLAIMER.

---

## ⚙️ ANDMED (oluline parandus)

`auto_adjust=True` on nüüd nii `backtest.py`-s kui `data_fetcher.py`-s → hinnad
arvestavad **dividende** ja indikaatorid (SMA/RSI/MACD) arvutatakse korrektsel,
katkematul hinnareal. Live-skaneerimine ja backtest kasutavad nüüd **samu** hindu.
(Varem `auto_adjust=False`, mis jättis dividendid arvestamata ja moonutas indikaatoreid.)

---

## 🔍 VÄLJUNDI SELGITUS

**Signal:** STRONG BUY > BUY > HOLD > SELL > STRONG SELL

**Confidence:**
- 🟢 high (0.7-1.0) - väga usaldusväärne
- 🟡 medium (0.3-0.7) - mõõdukas
- 🔴 low (< 0.3) - nõrk
- `adjusted_confidence` = confidence, mida fundamentals kiht modifitseerib.
- Filtreerimine kasutab nüüd ainult **technical confidence** (`confidence`), mitte `adjusted_confidence`.
- Confidence distribution kasutab venitamist ja väikest technical-rank spread’i.

**Rank:** 0.0–1.0 skoor, kõrgem = parem
- Rank on nüüd **hybrid modulaator**: `technical_rank * (0.5 + 0.5 * fundamental_score)`
- Bias mõju rankile on nüüd **sujuv**: `(fundamental_score - 0.5) * 0.1`
- Hybrid multiplier on kaitstud alumise piiriga (`>= 0.7`), et fundamentals ei suruks ranki liiga agressiivselt alla.
- Rank separation kasutab venitamist: `final_rank = final_rank ** 1.8`
- `fundamental_bias`: bullish / neutral / bearish (sekundaarne conviction-kiht)
- `fundamental_completeness`: kui palju fundamentals välju oli saadaval (0.0–1.0)
- `fundamental_factors`: valuation / growth / quality / risk
- `fundamental_interaction_penalty`: sektorite ja faktorite interaction adjustment
- `investment_type`: short_term_trade / high_conviction / long_term_candidate / mixed
- `--mode`: `growth | balanced | defensive | auto`
- Optional factor filters: `--min-growth-score`, `--max-risk-score`

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

# Factor-põhine filter (optional)
curl "http://localhost:8000/opportunities?market=ai&limit=10&mode=growth&min_growth_score=0.55&max_risk_score=0.70"

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
