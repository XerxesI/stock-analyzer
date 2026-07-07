# Research Protocol v1.2 — Swing Trade Signaali Valideerimine (KÜLMUTATUD)

**Staatus:** külmutatud (frozen). Muudatusi tehakse edaspidi ainult siis, kui need
tulenevad **tegelikest katsetulemustest** või **tõsisest metodoloogilisest veast**,
mitte oletustest ega täiendavast lihvimisest. Kõik kolm osapoolt (Claude, ChatGPT kolmes
voorus) on kinnitanud, et protokoll on implementeerimiseks piisavalt küps.

**Kontekst:** kolme AI arutelu tulemus stock-analyzer projekti swing trade mooduli
teadusliku valideerimisraamistiku kohta. Kõik varasemad testid (forward-return IC,
3 valimit) näitasid, et vale sihtmuutuja valik (fikseeritud kuupäeva tootlus) muutis
tulemused tõlgendamatuks. See dokument fikseerib metoodika.

**Muudatused v1.1 → v1.2 (ChatGPT kolmanda vooru tagasiside põhjal):**
- Punkt 3.2.1 ümbersõnastatud selgemaks (Data-driven vs Theory-driven hüpoteesid, mõlemad
  võrdselt legitiimsed — parem sõnastus kui v1.1 oma)
- Triple-barrier kordajad muudetud **konfiguratsiooniks**, mitte kõvakodeeritud väärtuseks
  (punkt 2.1)
- Min-n lävend muudetud soft/hard threshold'iks (punkt 4.4)
- VIX andmeallika jaoks lisatud 3-tasandiline fallback-ahel (punkt 4.3)
- Lisatud punkt 12: Signal Lifecycle / Negative Results Policy
- Lisatud punkt 13: Versioneerimine
- Lisatud punkt 14: `research_log.md` eksperimentide logi formaat

---

## 0. Miks see dokument eksisteerib

Eesmärk on vältida olukorda, kus valideerimisreegleid muudetakse kuude pärast alateadlikult
selleks, et mõni signaal "paremini töötaks". Kõik allolevad otsused fikseeritakse **enne**
uut testimisvooru. Muudatused pärast fikseerimist vajavad eraldi põhjendust ja peavad
olema dokumenteeritud (mitte vaikimisi tehtud).

---

## 0.1. Research Workflow (kogu protsessi raamistik)

See protokoll ei kirjelda ainult "kuidas valideerida ühte signaali", vaid kogu
uurimisprotsessi tervikuna:

```
Market Data (yfinance OHLCV)
        ↓
Feature Pipeline           (ainult minevikku kasutavad feature'id, punkt 6 checklist)
        ↓
Discovery Research         (event study / case-control — hüpoteeside GENEREERIMINE, punkt 2.5)
        ↓
Hypothesis Backlog         (registreeritud, veel testimata hüpoteesid — punkt 7.1)
        ↓
Signal Lab                 (triple-barrier + MFE/MAE + walk-forward valideerimine, punktid 2-4)
        ↓
Validated Signals          (kinnitatud, hold-out'iga läbinud signaalid — punkt 5)
        ↓
Trade Signal Engine        (lõplik komposiitmudel — alles pärast piisavat valideeritud signaalide hulka)
```

**Põhimõte:** signaal ei liigu kunagi otse "idee" → "Trade Signal Engine". Iga signaal
läbib Hypothesis Backlog'i (registreerimine enne testimist) ja Signal Lab'i (valideerimine
punktide 2-5 järgi) enne kui teda kaalutakse lõplikku mudelisse lisamiseks.

---

## 1. Hüpotees

**Üldhüpotees:** hinna/mahu-põhistel tehnilistel signaalidel (trend, momentum, RSI, support
proximity, ja tulevikus lisatavad) on mõõdetav, kuigi tõenäoliselt nõrk, ennustusjõud
selle üle, kas järgmise 5-40 kauplemispäeva jooksul tekib kasumlik swing trade
võimalus — eristi eri turu-režiimides.

**Miks see on parem kui eelmine hüpotees** ("kas hind tõuseb X% järgmise N päeva
jooksul"): eelmine hüpotees eiras, et swing trader väljub trade'ist dünaamiliselt
(kasumi juures või stop-lossi juures), mitte fikseeritud kuupäeval. Kolm varasemat testi
mõõtsid seetõttu valet asja — vt lisa A (varasemate testide kokkuvõte).

---

## 2. Sihtmuutuja (Target)

### 2.1. Triple-Barrier Labeling (peamine sihtmuutuja)

ATR-skaleeritud, mitte fikseeritud protsent — vältimaks kõrge-volatiilsusega aktsiate
süstemaatilist eelistamist/karistamist.

**Vaikeparameetrid — KONFIGURATSIOON, mitte kõvakodeeritud väärtus** (ChatGPT täpsustus
v1.2-s: kui hiljem selgub, et nt 3×ATR töötab paremini, ei tohi see nõuda protokolli
ega koodi ümberkirjutamist, ainult konfiguratsiooni muutmist):

```yaml
labeling:
  take_profit:
    atr_multiple: 2.0
  stop_loss:
    atr_multiple: 1.0
  max_holding_days: [5, 10, 20, 40]   # kõik neli testitakse paralleelselt, vt punkt 2.4
```

Algväärtus (2.0 üles / 1.0 alla) põhineb tüüpilisel swing trade 2:1 reward/risk
eeldusel — see on lähtepunkt, mitte lõplik tõde.

| Parameeter | Vaikeväärtus | Põhjendus |
|---|---|---|
| Ülemine barjäär (profit target) | +2.0 × ATR(14) | Asümmeetriline (2:1) reward/risk, tüüpiline swing trade eeldus |
| Alumine barjäär (stop-loss) | −1.0 × ATR(14) | Vt ülal |
| Ajaline barjäär | 5 / 10 / 20 / 40 päeva (testitakse kõiki neljal) | Mitmeid horisonte korraga, mitte üks fikseeritud valik (vt punkt 2.4) |

**Väljundmuutuja:** kategoriline — `UPPER_HIT` / `LOWER_HIT` / `TIME_HIT` (milline
barjäär realiseerus esimesena), pluss binaarne lihtsustus `SUCCESS = (UPPER_HIT)`.

**Arvutus:** iga bar `t` puhul vaadatakse `High`/`Low` (mitte `Close`) iga järgneva päeva
kohta kuni ajalise barjäärini, kontrollitakse, kumb barjäär (ülemine/alumine) tabas
kõigepealt.

### 2.2. MFE / MAE (Maximum Favorable / Adverse Excursion)

Arvutatakse **High/Low põhiselt**, mitte Close põhiselt, iga horisondi (5/10/20/40 päeva)
kohta eraldi:

- **MFE** = suurim hinnatõus (kõrgeima `High` ja `t` `Close` vahel) horisondi jooksul, %.
- **MAE** = suurim hinnalangus (madalaima `Low` ja `t` `Close` vahel) horisondi jooksul, %.

Need on **pidevad** mõõdikud (mitte kategoorilised) ja hoitakse triple-barrier
tulemusest **eraldi** — mitte komponeerituna üheks skooriks (vt punkt 5, "Quality Score"
otsus).

### 2.3. R-Multiple

Tuletatud MFE-st, mitte eraldi arvutatud: `R = MFE / ATR(14)`. Annab riskiga
normaliseeritud tootluse-mõõdiku, mis on võrreldav aktsiate vahel.

### 2.4. Horisondid

Testitakse **nelja** ajalist barjääri paralleelselt, mitte üht fikseeritud valikut:

| Horisont | Kontekst (miks testime) |
|---|---|
| 5 päeva | Breakout/lühiajaline momentum — indikaatorite sobivus siin kaheldav (order flow/mikrostruktuuri domeen), aga testime siiski |
| 10 päeva | Momentum jätk |
| 20 päeva | "Klassikaline" swing trade (originaalse 2-6 nädala vahemiku keskosa) |
| 40 päeva | Pikem swing / üleminek position trade'i suunas |

Iga signaali kohta raporteeritakse IC eraldi kõigi nelja horisondi kohta (vt näidistabel
punktis 4.5).

### 2.5. Discovery Research — hüpoteeside genereerimine (mitte kinnitamine)

**Eesmärk on erinev valideerimisest:** Discovery Research'i eesmärk EI OLE kinnitada, kas
mingi signaal töötab. Eesmärk on **avastada uusi hüpoteese**, mida seejärel Signal Lab'is
(punktid 2-4) rangelt testitakse. Discovery tulemus on alati "huvitav muster, mis vajab
testimist" — mitte kunagi "see signaal töötab".

**Faasis 1 ei ehitata keerulist automaatset pattern-mining süsteemi.** Esimene praktiline
Discovery on lihtne:

- **Event Study:** analüüsi N (nt 500-1000) suurimat hinnatõusu ja N suurimat hinnalangust
  uurimisperioodi andmetel, otsi ühiseid tehnilisi tunnuseid enne sündmust.
- **Case-Control Analysis:** vali õnnestunud breakout'id (kontrollpiiritletud definitsiooni
  järgi, nt +X% Y päeva jooksul) ja ebaõnnestunud breakout'id (sarnane algseis, aga edasi
  ei liikunud), võrdle tunnuseid.

**Oluline distsipliin:**
- Discovery käib **ainult uurimisperioodi** andmetel, mitte kunagi hold-out'il.
- Discovery väljund on **Hypothesis Backlog kirje** (vt punkt 7.1), mitte otsene järeldus
  raportisse.
- Iga Discovery'st tekkinud hüpotees läbib täpselt sama Signal Lab valideerimise
  (triple-barrier, walk-forward, hold-out) kui iga teine signaal — Discovery ei anna
  "kiirrada" valideerimisest mööda.

---

## 3. Feature'id (Signaalid)

### 3.1. Feature Pipeline arhitektuur

```
Raw OHLCV (yfinance)
        ↓
Feature Engineering  (core/indicators.py laiendus — kõik kausaalsed, vt punkt 6)
        ↓
Signal Registry      (iga signaal eraldi moodul, ühtne liides + minimaalne metadata)
        ↓
Labeling             (triple-barrier + MFE/MAE + R-multiple, punkt 2)
        ↓
Validation Framework (walk-forward IC, hold-out, turu-režiimi silt, punkt 4)
        ↓
Signal Comparison Report (koond-tabel kõigi signaalide/horisontide/režiimide kohta)
```

Feature Pipeline eristab "toorest arvutusest" (nt MACD väärtus) "signaaliks" (nt "MACD
histogramm paraneb" — juba tõlgendatud/binaarne). Kui MACD, MACD histogramm, MACD slope
jne kõik pärinevad samast alusarvutusest, arvutatakse see üks kord, mitte viis korda.

### 3.2. Praegused signaalid grupeerituna "turukäitumise nähtuse" järgi

Eesmärk ei ole leida "parim signaal", vaid **iga nähtuse kohta 1-2 kõige informatiivsemat
ja vähim-korreleeruvat esindajat**. Praegused 4 signaali:

| Nähtus | Praegused kandidaadid | Märkus |
|---|---|---|
| Trend | SMA50, SMA200, Golden Cross | Need kolm tõenäoliselt tugevalt korreleeritud — üks esindaja tõenäoliselt piisab |
| Momentum | MACD, MACD histogramm | Samuti potentsiaalselt korreleeritud omavahel |
| Mean-reversion/Momentum (segane) | RSI (toores, pidev) | Ainuke faktor varasemas testis nõrga positiivse IC-ga |
| Support/Location | Support zone proximity + bounce | Ei näidanud varasemas testis selget IC-d isoleeritult |

Tulevased kandidaadid (faas 4+, ainult pärast praeguste retesti):

| Nähtus | Kandidaadid |
|---|---|
| Money Flow | Relative Volume (RVOL), OBV, Accumulation/Distribution |
| Volatility Contraction | ATR contraction, Bollinger Band Width |
| Relative Strength | RS vs SPY, RS vs sektor |

Faasis 2 (retest) lisatakse korrelatsioonimaatriks olemasoleva 4 signaali väärtuste vahel,
et kontrollida otseselt, kas nt SMA50/SMA200/Golden Cross on tõesti redundantsed.

### 3.2.1. Hüpoteeside kaks legitiimset allikat (täpsustatud v1.2-s)

Hypothesis Backlog'il on kaks võrdselt legitiimset sisendkanalit:

**A. Data-driven Discovery** — tekib meie enda andmete uurimisest (event study,
case-control, pattern mining — punkt 2.5).

**B. Theory-driven Hypotheses** — tulevad akadeemilisest kirjandusest, practitioner-
kogemusest või tuntud faktoritest (nt Momentum, Relative Strength, RVOL, OBV, ATR
contraction — Faas 4 kandidaadid).

**Oluline pole hüpoteesi päritolu, vaid see, et see on enne testimist täpselt
defineeritud** (parameetrid, arvutusvalem, oodatav suund fikseeritud enne, kui
esimesed tulemused nähtavad). Mõlemad allikad läbivad identse Signal Lab valideerimise
(punktid 2-5) ja registreeritakse Hypothesis Backlog'is (punkt 7.1) enne testimist.

### 3.3. Feature metadata (minimaalne, mitte formaalne skeem)

Lihtne Python dict iga signaali juures, ilma valideerimis-skeemita praegu:

```python
SIGNALS = {
    "rsi": {"category": "momentum", "requires_volume": False, "output": "continuous"},
    "macd": {"category": "momentum", "requires_volume": False, "output": "continuous"},
    "trend_sma": {"category": "trend", "requires_volume": False, "output": "binary"},
    "support_proximity": {"category": "location", "requires_volume": False, "output": "continuous"},
}
```

Formaalne JSON-skeem lisatakse alles siis, kui signaale on ~10+.

---

## 4. Valideerimismeetod

### 4.1. Walk-forward + kausaalsus

Jätkuvalt sama põhimõte, mis eelmistes testides: iga kuupäeva `t` juures kasutatakse
**ainult** andmeid kuni `t`-ni (indikaatorid, support zones, ATR — kõik tagasivaatavad).
Label (triple-barrier tulemus, MFE/MAE) kasutab **ainult** infot `t+1` kuni `t+horisont`.
Vt punkt 6, look-ahead checklist.

### 4.2. Hold-out periood

Andmed jaotatakse ajaliselt kaheks:

- **Uurimisperiood (esimesed ~80% andmetest, vanemad andmed):** siin toimub kogu
  eksploratiivne töö — signaalide testimine, korrelatsioonianalüüs, Discovery Research,
  hüpoteeside genereerimine. Lihtne ajaline split (mitte rolling/expanding window),
  ChatGPT soovitusel — lihtsam, arusaadavam, väldib tulevikuinfo leket. Keerukamaid
  meetodeid (rolling walk-forward) kaalutakse alles siis, kui raamistik on stabiilne.
- **Hold-out periood (viimased ~20% andmetest, miinus horisondi-puhver):** puudutatakse
  **ainult üks kord**, kinnitava
  (confirmatory) testina, pärast seda kui uurimisperioodil on signaal/hüpotees juba
  valitud. Kui hold-out tulemus ei kinnita uurimisperioodi tulemust, loetakse signaal
  **mitte-kinnitatuks**, mitte ei minda tagasi parameetreid "parandama" ja hold-out'i
  uuesti testima (see rikuks kogu mõtte).

### 4.3. Turu-režiim (Faas 1: kaks mõõdet)

| Mõõde | Definitsioon Faasis 1 | Kategooriad |
|---|---|---|
| Trend | SPY Close vs SPY SMA200 | Bull / Bear |
| Volatiilsus | Vt allpool prioriteediahel | Low (<15) / Normal (15-25) / High (>25), skaala kohandub andmeallika järgi |

**Volatiilsuse andmeallika prioriteediahel** (ChatGPT täpsustus v1.2-s, robustsuse jaoks):
1. **`^VIX`** otse yfinance kaudu (esmavalik, lihtsaim)
2. **SPY realiseeritud volatiilsus** (nt 20-päevane rullhindade standardhälve,
   annualiseeritud) — fallback, kui VIX andmed pole kättesaadavad/usaldusväärsed
3. **ATR(SPY)** — viimane fallback, kui ka realiseeritud volatiilsuse arvutus ebaõnnestub

Kombinatsioon annab kuni 6 režiimi (2×3). Iga vaatlus (kuupäev+ticker) saab režiimi-sildi.
**Laiendatav** hiljem (breadth, sektori-rotatsioon) ilma andmestruktuuri muutmata — lisatakse
lihtsalt uue veeruna.

### 4.4. Feature Stability (segmendianalüüs) — DIAGNOSTILINE, MITTE CONFIRMATORY

Kriitiline reegel, mille kõik kolm osapoolt lõpuks kinnitasid: segmendianalüüs
(signaal × režiim × market-cap × sektor) **ei tohi** olla "otsime läbi kõik lõiked, kuni
midagi töötab" (see on p-hacking). Selle asemel:

1. Segmenti testitakse **ainult siis**, kui on **enne testimist** sõnastatud konkreetne
   hüpotees (nt "RVOL peaks töötama paremini kõrge-ATR nišis, sest kõrge volatiilsusega
   aktsiates on mahu-anomaaliad informatiivsemad").
2. Segmendi valim peab läbima kaheastmelise läve (ChatGPT täpsustus v1.2-s):
   - **Hard minimum = 50 vaatlust:** allpool seda ei tehta segmendianalüüsi üldse.
   - **Soft threshold = 200 vaatlust:** 50-200 vahel tehakse exploratory analüüs, aga
     tulemus märgitakse automaatselt sildiga **"Low Statistical Confidence"** —
     see ei keela analüüsi, aga hoiatab tõlgendamise juures.
3. Iga segmenditud leid, mis mõjutab lõplikku otsust, peab **kinnituma ka hold-out
   perioodil** (punkt 4.2), mitte ainult uurimisperioodil.
4. Kõik testitud (mitte ainult "õnnestunud") segmendid dokumenteeritakse
   Signal Comparison Report'is — valikuline raporteerimine ainult positiivsetest
   tulemustest on keelatud.

### 4.5. Multiple Testing kaitse

Iga signaali/horisondi/režiimi kombinatsioon on eraldi test. Kuna kombinatsioonide arv
kasvab kiiresti (nt 4 signaali × 4 horisonti × 6 režiimi = 96 testi juba praegusel
signaalide hulgal), rakendatakse:

- **Bonferroni-tüüpi korrektsioon** või lihtsam praktiline reegel: IC-d, mis on statistiliselt
  olulised ainult ühel horisondil/režiimil paljude testitute seast, käsitletakse
  **kahtlusega**, kuni need kinnituvad hold-out'il.
- Kõik testid (ka ebaõnnestunud) dokumenteeritakse — vältimaks "valikulise raporteerimise"
  moonutust.

Näidistabel (illustreeriv, ChatGPT eeskujul):

```
Signaal: RVOL
  5 päeva  IC = 0.12
  10 päeva IC = 0.08
  20 päeva IC = 0.03
  40 päeva IC = -0.01
→ Tõlgendus: signaal töötab lühiajaliselt, mitte pikaajaliselt.
```

---

## 5. Edukuse / hülgamise kriteeriumid

**Signaal loetakse "huvipakkuvaks" (mitte veel "kinnitatuks"), kui uurimisperioodil:**
- IC ületab müra-läve (nt |IC| > 0.03-0.05, arvestades autokorrelatsiooni-korrigeeritud
  efektiivset valimi suurust, mitte toorest rea-arvu)
- Muster on konsistentne vähemalt kahel naaber-horisondil (mitte ainult üks üksik
  horisont juhuslikult)

**Signaal loetakse "kinnitatuks", kui lisaks:**
- Sama suund/suurusjärk kordub hold-out perioodil
- Efekt ei kao täielikult ühelgi peamisel turu-režiimil (või kui kaob, on see
  dokumenteeritud kui "režiimi-spetsiifiline", mitte üldine signaal)

**Signaal hüljatakse, kui:**
- IC jääb müra piiresse kõigil horisontidel/režiimidel uurimisperioodil, VÕI
- Uurimisperioodi tulemus ei kinnitu hold-out'il

**Quality Score (komposiitmõõdik) EI EHITATA enne**, kui üksikud komponendid
(Success/Failure, MFE, MAE, R-multiple) on iga signaali kohta eraldi valideeritud. See
väldib Trade Score v1 viga (kolm komponenti kokku pandud enne, kui teati, kas ükski neist
üksi midagi ennustab).

---

## 6. Look-Ahead Bias ja Data Leakage — kohustuslik checklist

Iga uue signaali/labeli lisamisel kontrollitakse enne valideerimist:

- [ ] Kõik feature'id (indikaatorid, support zones, ATR, RVOL jne) kasutavad arvutamisel
      **ainult** andmeid kuni hetkeni `t` (mitte kogu ajalugu, mis lõikub hiljem `t`-ni)
- [ ] Label (triple-barrier tulemus, MFE, MAE) kasutab **ainult** andmeid `t+1` kuni
      `t+horisont`
- [ ] Ükski feature ei kasuta implitsiitselt tulevikuinfot (nt globaalselt arvutatud
      normaliseerimis-statistikat, mis on tuletatud kogu andmestikust, sh tulevikust)
- [ ] Universumi valik (millised tickerid testitakse) ei ole valitud tulevikuteadmise
      põhjal (survivorship bias — vt lisa A, osaliselt leevendatud, mitte lahendatud)

---

## 7. Exploratory vs Confirmatory — selge eristus

- **Exploratory (uurimisperiood):** kõik hüpoteeside genereerimine, signaalide
  võrdlemine, segmentide uurimine (punkti 4.4 reeglite piires), korrelatsioonianalüüs,
  Discovery Research (punkt 2.5). Tulemused on **kandidaadid**, mitte järeldused.
- **Confirmatory (hold-out periood):** puudutatakse üks kord, ainult eelnevalt valitud
  hüpoteeside/signaalide kinnitamiseks. Tulemus on **lõplik** selle tsükli jaoks — kui
  ei kinnitu, signaal ei lähe edasi järgmisesse faasi.

### 7.1. Hypothesis Classification — formaalne metaandmete väli

Iga testi/tulemuse kirje (nt Signal Comparison Report'i rida) peab kandma eksplitsiitset
silti, mitte jääma ainult dokumendi tasandile:

```python
{
    "hypothesis_id": "rvol_high_atr_2024",
    "source": "literature" | "discovery_research",   # vt punkt 3.2.1
    "registered_date": "2026-07-08",                 # enne testimist fikseeritud
    "analysis_type": "exploratory" | "confirmatory",
    "status": "untested" | "exploratory_support" | "confirmed" | "rejected",
}
```

**Hypothesis Backlog** on selliste kirjete lihtne register (nt CSV/JSON fail või
tabel andmebaasis) — iga uus hüpotees (kummast allikast tahes) saab kirje **enne**
testimise algust, staatusega `untested`. Testimise järel uuendatakse `status` väli.
See teeb "valikulise raporteerimise vältimise" (punkt 4.5) kontrollitavaks, mitte ainult
lubaduseks: backlog'ist on nähtav iga hüpotees, mis kunagi testiti, sh need, mis
tagasi lükati.

---

## 8. Kaustastruktuur (implementeerimise alus)

```
stock_analyzer/
  signals/
    registry.py                          # SIGNALS dict + registreerimisloogika
    trend.py                             # SMA50/200, Golden Cross
    momentum.py                          # MACD, MACD histogramm
    rsi.py                               # RSI (pidev)
    support.py                           # support zone proximity + bounce
  validation/
    labeling.py                          # triple-barrier (ATR-skaleeritud) + MFE/MAE + R-multiple
    regime.py                            # trend + volatiilsuse silt (2 mõõdet, laiendatav)
    ic_test.py                           # walk-forward IC, mitme horisondi tugi, hold-out split
    feature_stability.py                 # diagnostiline segmendianalüüs (punkt 4.4 reeglite järgi)
  discovery/
    event_study.py                       # suurimate tõusude/languste ühised tunnused (punkt 2.5)
    case_control.py                      # õnnestunud vs ebaõnnestunud breakout'ide võrdlus
    hypothesis_backlog.py                # registri loogika (punkt 7.1) — CSV/JSON põhine
  evaluation/
    signal_comparison_report.py          # koond-raport: kõik signaalid × horisondid × režiimid
```

---

## 9. Faaside plaan

| Faas | Sisu | Väljund |
|---|---|---|
| **0 (see dokument)** | Research Protocol kooskõlastamine | Fikseeritud metoodika |
| **1** | Feature Pipeline + Labeling (triple-barrier, MFE/MAE, R-multiple) + Validation Framework (walk-forward, hold-out split, turu-režiimi silt, mitme horisondi tugi) | Töötav, testitud raamistik, veel ilma järeldusteta |
| **2** | Olemasoleva 4 signaali retest uue raamistikuga, sh korrelatsioonimaatriks (kas SMA50/SMA200/Golden Cross on redundantsed) | Signal Comparison Report v1 |
| **3** | Feature Independence — iga "nähtuse" kohta 1-2 esindaja valik korrelatsiooni põhjal | Vähendatud, sõltumatute signaalide nimekiri |
| **3.5** | Discovery Research esimene praktiline versioon (lihtne event study + case-control, punkt 2.5) uurimisperioodi andmetel — täiendab Hypothesis Backlog'it | Hypothesis Backlog v1 (kirjanduspõhised + discovery-põhised hüpoteesid koos) |
| **4** | Hypothesis Backlog'i kandidaadid (nii kirjanduspõhised — RVOL, OBV, A/D, ATR contraction, Bollinger Width, Relative Strength — kui ka Discovery'st tekkinud) testitakse sama Signal Lab raamistiku kaudu | Laiendatud Signal Comparison Report |
| **5** | Entry Timing (nõuab "breakout" definitsiooni — eraldi alamprojekt), mitmemõõtmeline turu-režiim (breadth, sektori-rotatsioon, likviidsus) | — |

---

## 10-11. Lahtised otsused — LAHENDATUD (v1.2)

Kõik kolmandas voorus veel lahtised küsimused said lahenduse, mis on juba dokumenti
integreeritud:

| Küsimus | Lahendus | Kus |
|---|---|---|
| Triple-barrier kordajad | Konfiguratsioon (YAML), vaikeväärtus 2.0/1.0 | Punkt 2.1 |
| Min-n segmendianalüüsiks | Soft/hard threshold (50/200) | Punkt 4.4 |
| VIX andmeallikas | 3-tasandiline fallback-ahel | Punkt 4.3 |
| Kirjanduspõhiste kandidaatide staatus | Data-driven vs Theory-driven, mõlemad legitiimsed | Punkt 3.2.1 |

**Protokoll on nüüd külmutatud (vt lehe algus).** Järgmised muudatused toimuvad ainult
tegelike katsetulemuste või tõsise metodoloogilise vea põhjal.

---

## 12. Signal Lifecycle / Negative Results Policy

Iga signaal liigub selgelt defineeritud olekute vahel — see on kriitiline, kui aasta
pärast on 30 signaali ja vaja on meeles pidada/dokumenteerida, miks mõni neist kõrvale
jäeti:

```
Candidate  →  Validated  →  Weak Evidence  →  Deprecated
```

| Olek | Kriteerium |
|---|---|
| **Candidate** | Registreeritud Hypothesis Backlog'is (punkt 7.1), veel testimata |
| **Validated** | Läbis punkti 5 "kinnitatud" kriteeriumid (uurimisperiood + hold-out mõlemad kinnitavad) |
| **Weak Evidence** | Mõni exploratory tugi olemas, aga ei kinnitunud hold-out'il, VÕI kinnitus on režiimi-spetsiifiline |
| **Deprecated** | Selgelt tagasi lükatud (IC müra piires kõigil horisontidel/režiimidel), VÕI asendatud parema esindajaga sama "nähtuse" kategooriast (punkt 3.2) |

**Reegel:** signaali oleku muutus (eriti "Deprecated") dokumenteeritakse alati koos
põhjendusega Hypothesis Backlog'is — vaikimisi kustutamine ilma jälgita on keelatud.

---

## 13. Versioneerimine

Kolm eraldi versiooninumbrit, uuendatakse sõltumatult:

- **Research Protocol** (see dokument) — praegu v1.2
- **Signal Lab** (validation/ + signals/ kood) — algab v1.0-st implementeerimise algul
- **Trade Engine** (lõplik komposiitmudel, kui/kui piisavalt signaale on valideeritud) — algab v0.x-st (mitte-produktsiooniküpse tähisena)

Iga oluline tulemus (nt Signal Comparison Report) viitab kõigi kolme versiooni numbrile,
et hilisem tulemuste võrdlus oleks üheselt mõistetav (nt "see IC arvutati Signal Lab
v1.3 ja Research Protocol v1.2 all").

---

## 14. `research_log.md` — eksperimentide logi

Iga katse (mitte ainult "õnnestunud") saab kirje lihtsas, inimloetavas failis:

```markdown
### Experiment 001
- **Hypothesis:** RVOL ennustab positiivselt 10-päeva forward MFE-d kõrge-ATR aktsiatel
- **Dataset:** Juhuslik 300-tickeri valim (seed=42), NASDAQ+NYSE+NYSE American
- **Period:** Uurimisperiood (80%), 2023-07 kuni 2025-10
- **Target:** Triple-barrier (2.0×ATR / 1.0×ATR), horisont 10 päeva
- **Features:** RVOL(20), segmenditud ATR kvartiilide kaupa
- **Result:** IC = 0.04 (kogu valim), IC = 0.09 (kõrgeim ATR kvartiil, n=612)
- **Conclusion:** Nõrk üldine signaal, tugevam kõrge-ATR segmendis — kandidaat hold-out
  kinnituseks kõrge-ATR nišis (Weak Evidence → Validated kandidaat)
```

See fail kasvab orgaaniliselt iga testimisvooruga ja muutub kuude jooksul asendamatuks
referentsiks selle üle, mida on juba proovitud ja miks.

---

## Lisa A: Varasemate testide kokkuvõte (kontekst)

Vt eraldi dokumenti "Swing Trade Signaali Valideerimine — kokkuvõte" (jagatud eelnevalt).
Lühikokkuvõte: kolm testi (thematic 30 tickerit, kureeritud large-cap 204 tickerit,
juhuslik valim 300 tickerit) fikseeritud-kuupäeva forward-return sihtmuutujaga näitasid
nõrka/negatiivset IC-d, kusjuures BUY-klassifikatsioon andis konsistentselt halvima
forward-tootluse kõigis kolmes valimis. Peamine järeldus: probleem oli suure tõenäosusega
sihtmuutuja valikus (fikseeritud forward-return), mitte tingimata signaalide endi
puudumises ennustusjõust — mistõttu see uus protokoll muudab sihtmuutujat, mitte (veel)
signaale.
