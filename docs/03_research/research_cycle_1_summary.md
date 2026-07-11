# Swing Trade Signaali Valideerimine — Research Cycle #1 kokkuvõte

**Kontekst:** stock-analyzer projekti swing trade mooduli esimene täielik teaduslik
valideerimistsükkel, alates Research Protocol v1.2 külmutamisest kuni esimese
Locked Test'i ja praktiliste mõõdikuteni. See dokument on mõeldud teisele AI-le
(ChatGPT) lõplikuks ülevaatuseks — sisaldab kogu metoodikat ja tulemusi ilma
eelneva vestluse kontekstita.

---

## 1. Miks ja kuidas testisime (lühikokkuvõte metoodikast)

**Algne probleem:** varasem valideerimine kasutas fikseeritud-kuupäeva forward-return'it
sihtmuutujana, mis ei suutnud eristada "tõusis kiiresti, siis tõmbus tagasi" (edukas
swing trade) ja "ei liikunud kunagi" (tõeline ebaõnnestumine). See muutis kogu varasema
testitulemuse tõlgendamatuks.

**Lahendus (Research Protocol v1.2):**
- **Triple-barrier labeling**, ATR-skaleeritud (mitte fikseeritud protsent): ülemine
  barjäär +2.0×ATR(14), alumine barjäär −1.0×ATR(14), ajaline barjäär 5/10/20/40 päeva
  paralleelselt
- **MFE/MAE** High/Low põhiselt (mitte Close), **R-multiple** = MFE/ATR
- **Turu-režiimi silt**: trend (SPY vs SMA200: Bull/Bear) × volatiilsus (VIX→SPY
  realiseeritud volatiilsus→ATR% tertsiilid fallback-ahel: Low/Normal/High)
- **80/20 ajaline train/holdout split**
- **Kolmetasandiline andmete lukustus**: Train (eksploratiivne) → Holdout (esimene
  kinnitav vaade, "kulub" ära, kui kasutatakse hüpoteeside täpsustamiseks) → **Locked
  Test** (täiesti uus, seni puutumata 300-tickeri valim, ruumiline mitte ajaline lukk,
  kuna tuleviku-andmeid pole)
- **Diagnostiline vs confirmatory eristus**: segmendianalüüs vajab enne testimist
  sõnastatud hüpoteesi, mitte "otsime läbi kõik lõiked"
- **Signal Lifecycle**: Candidate → Promising → Conditional → Validated → Core (või
  Archived, kui tagasi lükatud)

---

## 2. Faas 2: olemasoleva 4 signaali retest uue raamistikuga

Testisime **trend, momentum, RSI, support** signaale (Trade Score v2 komponendid,
mitte kombineeritud skoor) 300-tickeri juhusliku valimi peal (NASDAQ+NYSE+NYSE
American, seed=42), walk-forward IC-ga (train vs holdout, kõik 4 horisonti).

**Esimesed tulemused (train vs holdout, IC vs R-multiple, 20-päeva horisont):**

| Signaal | Train IC | Holdout IC |
|---|---|---|
| Trend | -0.0009 | -0.0394 |
| Momentum | +0.0049 | **+0.0822** |
| RSI | +0.0117 | **+0.0548** |
| Support | +0.0137 | +0.0234 |

**Anomaalia:** Momentum/RSI holdout IC oli dramaatiliselt suurem kui train IC —
vastupidine tavapärasele üleõppimise mustrile.

---

## 3. Deep-dive: anomaalia diagnoosimine

Kolm paralleelset kontrolli (block bootstrap CI sümbolite tasandil, rolling 6-kuu
window IC, train vs holdout režiimi jaotus):

- **Režiimi jaotus:** train'is `Bull_Low`=10.7%, holdout'is 0.0% (kadus täielikult);
  holdout'is `Bull_High`=7.7%, train'is 0.0%. Kokku kõrge-volatiilsuse päevi
  (Bear_High+Bull_High): train 6.3%, holdout 15.7% — **2.5x rohkem**.
- **Bootstrap CI:** Trend null mõlemas perioodis; Momentum/RSI train CI sisaldab
  nulli, holdout CI **välistab** nulli; Support vastupidi (train CI välistab nulli,
  holdout ei — tõenäoliselt statistilise võimsuse, mitte suuna probleem, kuna
  punktihinnang holdout'is oli tegelikult kõrgem, lihtsalt väiksema valimiga).
- **Rolling window:** Momentum IC liikus järkjärgult -0.093 (2024 algus) → +0.048
  (2026 algus) — järkjärguline nihe, mitte üks juhuslik episood.
- **Korrelatsioonimaatriks:** Momentum-RSI r=0.68 (kattuvad osaliselt), Support
  praktiliselt sõltumatu kõigist (|r|<0.06).

**Järeldus:** holdout'i "anomaalia" seletub suuresti sellega, et holdout periood
sisaldas objektiivselt rohkem kõrge-volatiilsuse/Bear päevi, kus Momentum/RSI juba
teadaolevalt paremini töötavad — mitte puhas juhus.

**Kriitiline metodoloogiline probleem, mille tuvastasime:** kuna kasutasime holdout'i
üksikasjalikult selle mustri **avastamiseks**, ei saa me sama holdout'i enam kasutada
täpsustatud hüpoteesi **kinnitamiseks** (test set contamination / ringtõestus).

---

## 4. Faas 3: incremental information test (ainult train, holdout puutumata)

Support + Momentum lihtne võrdse-kaaluga z-score kombinatsioon:
- Support üksi: IC=+0.0137, Momentum üksi (train, tingimusteta): IC=+0.0049,
  Kombineeritud: IC=+0.0164 — marginaalne, mitte veenev tõus
- **Selgitus:** Momentum'i train-perioodi IC on ligilähedal nullile, kuna train
  sisaldas vähem Bear-episoode — tingimusteta kombinatsioon ei testinud tegelikku
  hüpoteesi
- Interaktsiooni-analüüs (diagnostiline): Momentum'i IC erines märgilt kahe Support
  kvartiili vahel (+0.010 vs -0.011) — intrigeeriv, aga nõrk ja mitte veel usaldatav

**Täpsustatud, pre-registered hüpotees enne Locked Test'i:**
> Momentum signaali IC on positiivne Bear-režiimides (Bear_High, Bear_Normal) ja
> nulli-lähedane/negatiivne Bull-režiimides — mitte "kõrge volatiilsus üldiselt"
> (Bull_High näitas tegelikult IC=-0.027, negatiivne).

---

## 5. Locked Test: kolm pre-registered hüpoteesi, üks kord, uus 300-tickeri valim (seed=123)

**Enne testimist külmutatud:**
- **M1 (Conditional Momentum):** Bear (SPY<SMA200) vs Bull (SPY≥SMA200), momentum_signal
  muutmata kujul, primary horizon=20d, primary target=R-multiple, oodatud tulemus:
  IC_bear>0 JA IC_bear>IC_bull
- **S1 (Support Replication):** IC>0 üldiselt, ei pöördu enamikus režiimides
- **C1 (Incremental Information):** külmutatud valem
  `C1 = z_support + z_momentum × I(Bear)`, parameetrid (μ,σ) fititud train+holdout
  peal, rakendatud muutmata kujul Locked Test'ile. Oodatud: IC(C1) > IC(Support üksi)

**Tulemused:**

```
M1: IC_bear = +0.0733 (n=3076)   IC_bull = +0.0065 (n=24698)   → CONFIRMED
S1: IC_overall = +0.0092 (n=27774)                              → CONFIRMED
    (per-regime: Bear_High +0.0107, Bear_Normal -0.0056, Bull_High +0.0057,
     Bull_Low +0.0269, Bull_Normal +0.0075 — 1/5 regime pöördus, lubatud lävendi piires)
C1: IC(support alone) = +0.0092   IC(C1) = +0.0163               → CONFIRMED
    (kordub kõigil 4 horisondil: 5d 0.0144→0.0217, 10d, 20d, 40d 0.0079→0.0151)
```

**Kõik kolm hüpoteesi kinnitusid.**

**Oluline sõltumatuse piirang (dokumenteeritud enne testi, mitte tagantjärele
vabandusena):** Locked Test annab **ristlõikelist**, mitte **ajalist** sõltumatust —
uued tickerid, aga sama kalendriperiood, sama makrorežiim kui kõik varasemad testid.
"CONFIRMED" tähendab "kehtib väljaspool algset 300-tickeri valimit", mitte "kehtib
teistsuguses turutsüklis".

---

## 6. Praktilised mõõdikud: kas see on kaubeldav, mitte ainult statistiliselt oluline?

**Probleem esimeses katses:** win rate mõõdik oli identne kõigi kolme signaali jaoks
(0.337), kuna arvutati üle terve populatsiooni, mitte signaali kvantiili lõikes —
ebainformatiivne disainiviga, parandatud.

**Parandatud top-20% success rate + lift, globaalselt (kõik režiimid koos):**

| Signaal | Baseline | Top-20% | Lift |
|---|---|---|---|
| Support (kõik režiimid) | 33.7% | 34.3% | 1.020x |
| Momentum (ainult Bear) | 31.9% | 32.8% | 1.027x |
| C1 (kõik režiimid) | 33.7% | 34.3% | 1.020x |

**Üllatav esialgne leid:** C1 lift oli identne Support'i lift'iga globaalses raamis —
tundus, nagu kombinatsioon ei annaks midagi juurde, vastupidiselt IC-tulemusele.

**Selgitus ja parandus:** Bear moodustab ainult ~11% kõigist vaatlustest — globaalne
top-20% lävend domineerib Bull-režiimi kõrge-Support vaatlustega, lahjendades ära
Momentum'i Bear-spetsiifilise panuse. **Õigem test: top-20% eraldi Bear'i sees:**

| Signaal (Bear'i sees) | Baseline | Top-20% | Lift | Absoluutne tõus |
|---|---|---|---|---|
| Support üksi | 31.9% | 32.0% | 1.002x | +0.1 protsendipunkti |
| **C1 kombinatsioon** | 31.9% | **34.3%** | **1.073x** | **+2.4 protsendipunkti** |

**Terminoloogia parandus (ChatGPT tähelepanek):** C1 annab oma Bear-baseline'i suhtes
2.4 protsendipunkti absoluutset tõusu, mis on 1.073x (~7.3%) suhteline paranemine.
Lift'ide vahe (1.073−1.002=0.071) ei ole ise protsendipunkt — see on kahe suhtarvu
vahe. Eelmises versioonis oli see väärsõnastatud ("7 protsendipunkti lift'i") —
parandatud.

**Event deduplication** (kuna 5-päevane re-skoorimise samm + 20-päevane horisont
tähendab, et järjestikused vaatlused kattuvad ajaliselt): C1 top-20% globaalses
raamis andis 5651 toorvaatlust, aga ainult **2831 tõeliselt eraldiseisvat setup'it**
(2.00x inflatsioonifaktor). Bear-spetsiifilises tipp-grupis (n=642 enne dedup'i)
oleks deduplitseeritud tulemus hinnanguliselt ~320 tõelist setup'it kogu 288-tickerilise
universumi peale **3 aasta jooksul kokku** — haruldane, mitte igapäevane signaal.

---

## 7. Lõplik Signal Lifecycle seis

| Signaal | Staatus | Praktiline hinnang |
|---|---|---|
| Trend v1 | **Archived** | Null 4 sõltumatul meetodil (fixed-return, triple-barrier, bootstrap, rolling) — konkreetne implementatsioon sellel target'il/horisondil, mitte "trend üldse ei tööta" |
| Support v1 | **Validated Base Signal** | Väike, stabiilne, regime-sõltumatu; lift globaalselt ~1.02x, Bear'i sees ~1.00x. ChatGPT ettepanek: käsitleda edaspidi pigem **context feature'ina** ("kus hind asub, kui mõni teine nähtus aktiveerub"), mitte iseseisva trade trigger'ina — praktiline lift on liiga nõrk iseseisvaks kasutuseks. |
| Momentum v1 | **Validated Conditional Signal** | Ainult Bear-režiimis (IC 0.073 vs 0.0065 Bull'is); isoleeritult tagasihoidlik praktiline lift (~1.03x) |
| **C1 (Support + Bear-Momentum)** | **Validated Combination Candidate** | Reaalne, kitsas edge spetsiifiliselt Bear'is (lift 1.073x), harv esinemissagedus (~320 setup't/3a); "Candidate", mitte "Core", kuna ajaline replikatsioon (uus turutsükkel) puudub |

---

## 8. Peamised metodoloogilised õppetunnid sellest tsüklist

1. **Sihtmuutuja valik on olulisem kui indikaatorite valik.** Fikseeritud forward-return
   andis läbivalt negatiivseid/olematuid tulemusi kolmel valimil; triple-barrier
   R-multiple paljastas reaalseid, kuigi väikseid signaale samade alusandmete pealt.
2. **Test set contamination on reaalne risk isegi distsiplineeritud protsessis** — me
   ise sattusime sellesse (kasutasime holdout'i regime-mustri avastamiseks, siis pidime
   tunnistama, et ei saa sama holdout'iga kinnitada) ja pidime lahenduseks looma
   ruumilise (mitte ajalise) Locked Test'i.
3. **Globaalne top-kvantiili test võib peita regime-spetsiifilist efekti**, kui alamgrupp
   on väikeses vähemuses — režiimiteadlik mudel vajab režiimiteadlikku hindamist, mitte
   ainult režiimiteadlikku disaini.
4. **Statistiliselt kinnitatud IC (isegi bootstrap CI-ga) ei garanteeri suurt praktilist
   lift'i** — meie kinnitatud efektid (IC 0.01-0.07) andsid lift'e vahemikus 1.00x-1.07x,
   mitte dramaatilisi erinevusi.
5. **Event deduplication on kohustuslik enne coverage/praktilisuse hinnangut** — ilma
   selleta oleks "kui tihti signaal esineb" arv olnud ~2x üle hinnatud.

---

## 9. Cycle #2 Step 1: C1 diagnostiline profiil (monotoonsus + tickerite kontsentratsioon)

Enne uute nähtuste (Relative Strength, Money Flow) juurde liikumist täitsime ChatGPT
nõutud viimase diagnostilise sammu: kas C1 edge suureneb monotoonselt lõike
kitsenedes, ja kas edge on koondunud vähestesse tickeritesse.

**Monotoonsuse tabel (Bear'i sees, horisont=20d):**

| Lõige | n (raw) | n (dedup) | Success rate | Lift | Mediaan MFE | Mediaan MAE | Mediaan R |
|---|---|---|---|---|---|---|---|
| All Bear | 3076 | – | 31.9% | 1.000x | +0.046 | −0.052 | +0.920 |
| Top 30% | 1020 | 538 | 32.6% | 1.023x | +0.040 | −0.040 | +1.006 |
| Top 20% | 642 | 396 | **34.3%** | **1.073x** | +0.039 | −0.036 | +1.066 |
| Top 10% | 393 | 260 | 33.1% | 1.036x | +0.033 | −0.034 | +1.059 |
| Top 5% | 154 | 120 | 32.5% | 1.017x | +0.032 | −0.032 | +0.895 |

**Mitte rangelt monotoonne** — tipp on Top20% juures, Top10%/Top5% näitavad madalamat
success rate'i.

**Statistiline kontroll (binoomjaotuse standardviga, SE=√(p(1−p)/n)):**

| Lõige | SE |
|---|---|
| Top 30% | ±1.5pp |
| Top 20% | ±1.9pp |
| Top 10% | ±2.4pp |
| Top 5% | ±3.8pp |

Top20% vs Top10% erinevus (1.2pp) < kombineeritud SE (~3.1pp). Top20% vs Top5%
erinevus (1.8pp) < kombineeritud SE (~4.2pp). Erinevused jäävad väiksemaks kui
ligikaudne binoomjaotuse standardviga ega anna tõendust sisulisest mittemonotoonsusest.

**ChatGPT parandus:** binoomjaotuse SE valem eeldab sõltumatuid Bernoulli vaatlusi.
Isegi pärast tickeripõhist deduplitseerimist võivad setup'id olla ajaliselt
klasterdunud (nt kümned aktsiad reageerivad samale nädalasele makrosündmusele/
turulangusele calendar-time clustering'u kaudu) — tegelik ebakindlus võib olla
tabelis toodud SE-st suurem. Need standardvead on seetõttu **kirjeldavad, mitte
lõplikud** ("descriptive rather than definitive"). Rangema kontrolli jaoks oleks
vaja block bootstrap'i kuupäevaplokkide (mitte tickerite) kaupa — see pole praegu
tehtud ja pole Cycle #2 jätkamise eeltingimus, aga on dokumenteeritud piirang.

**Tickerite kontsentratsioon (Top20% Bear'i sees, deduplitseeritud):**

- 396 deduplitseeritud setup'it, **241 unikaalset tickerit** (287-st, mis kunagi
  Bear'i sattusid)
- Mediaan **2 setup't tickeri kohta**, maksimaalne **3 setup't** ühelt tickerilt
- **Top-10 tickerite osakaal: ainult 7.6%** kõigist setup'itest

**Järeldus (parandatud sõnastus):** C1 edge ei ole kontsentreerunud üksikutesse
tickeritesse. **See EI tõesta**, et edge poleks kontsentreerunud mõnda sektorisse,
market-cap segmenti, kõrge-ATR/kõrge-beeta gruppi (nt AI/pooljuhid/krüpto-seotud) —
sektori-, market-cap- ja volatiilsuskontsentratsioon jäävad **eraldi, veel avamata
diagnostilisteks küsimusteks**. Need lisatakse hiljem kerge, riskidiagnostilise
raportina (mitte hüpoteeside kaevandamiseks) — ei blokeeri Cycle #2 algust.

C1 praktiline profiil loetakse nüüd **täielikuks ja külmutatuks**: (1) IC kinnitunud
Locked Test'il, (2) reaalne, kuigi tagasihoidlik lift (Top 20-30% piirkonnas,
kirjeldavalt stabiilne), (3) tickerite lõikes laialt hajutatud. **Edasisi C1
eksperimente ega kaaluoptimeerimist ei tehta** — järgmisena liigutakse Relative
Strength ja Money Flow nähtuste juurde.

**ChatGPT kinnitus:** jah, C1 praktilise profiili võib lugeda piisavalt lõpetatuks.
Staatus jääb **Validated Combination Candidate** (mitte Core, kuni ajaline
replikatsioon puudub).

## 10. Cycle #2 Step 2: Relative Strength (RS1/RS_slope/RS_accel) — tulemus ja järeldus

Testisime kolme Relative Strength signaali (kõik "aktsia vs SPY" kujul, ilma sektori
võrdluseta — vt allpool RS2 piirang) dev-valimil (seed=42, sama 300-tickeriline
valim, mida Cycle #1 kasutas). Metoodika: causal walk-forward IC, 80/20 train/holdout,
primary lookback/horisont=20 päeva, secondary=10/40 päeva (mitte 5 — pre-registered
enne testimist, et vältida hilisemat horisondi valimist), Bull/Bear lõige sisse
ehitatud algusest peale.

**RS1 (aktsia return − SPY return, 20-päevane libisev aken):**

| Horisont | Train IC | Holdout IC |
|---|---|---|
| 10d (secondary) | +0.0351 | +0.0025 |
| 20d (PRIMARY) | +0.0185 | −0.0125 |
| 40d (secondary) | +0.0108 | −0.0156 |

Režiimi lõige (20d): Train Bull +0.0207, Train Bear +0.0152; Holdout Bull −0.0082,
Holdout Bear −0.0323.

**RS_slope (RS muutus 5-päevase akna vältel):**

Train Bull +0.0044, Train Bear **+0.0674**; Holdout Bull −0.0100, Holdout Bear
**−0.0952**.

**RS_accel (RS_slope teine tuletis):**

Train Bull +0.0049, Train Bear **+0.0598**; Holdout Bull +0.0044, Holdout Bear
**−0.1523**.

### Esialgne tähelepanek: sama-režiimi-sisene märgi pöördumine

Erinevalt Momentum'i/RSI mustrist (train≈0, holdout tugevalt positiivne, seletatav
režiimi-koostise nihkega), näitas RS_slope/RS_accel **täieliku märgi pöördumise SAMA
Bear-kategooria sees** (nt rs_accel: +0.060 train → −0.152 holdout). Kuna mõlemad
väärtused on individuaalselt piisavalt suured, et olla ~2-3 standardviga nullist
eemal (ligikaudne SE train n=2401 juures ~0.020, holdout n=560 juures ~0.042), ei
saanud seda kohe müraks pidada — vajas täiendavat kontrolli.

### Rolling window diagnostika (6-kuu aknad, Bear-alamvalim eraldi)

| Aken | RS1 | RS_slope | RS_accel |
|---|---|---|---|
| 2025-03-31 (n=801) | +0.0601 | −0.0542 | −0.0256 |
| 2025-09-30 (n=1600) | −0.0091 | +0.1061 | +0.0818 |
| 2026-03-31 (n=285) | +0.0224 | −0.0071 | −0.0596 |
| 2026-09-30 (n=275) | −0.0738 | −0.1140 | −0.1909 |

**Järeldus:** muster on **erratiline, mitte järkjärguline** — märk hüppab aken-akna
kaupa ilma trendita (võrreldes Momentum'i sujuva -0.093→-0.031→+0.050→+0.007→+0.048
progressiooniga). See, koos asjaoluga, et Bear-vaatlusi tekkis üldse alles alates
2025. märtsist (varasem periood oli valdavalt Bull, muutes väikese Bear-valimi eriti
müra-altiks), viitab tugevalt sellele, et **varasem "train vs holdout pöördumine" oli
kahe erineva müra-hetke juhuslik kokkusattumus**, mitte reaalne, kuigi ebastabiilne
efekt.

### Lõplik järeldus RS1/RS_slope/RS_accel kohta

**Ei näita stabiilset signaali kummaski režiimis.** Bull-aknad (palju suuremad
valimid, n=1056-6735) hõljuvad samuti nulli lähedal (-0.039 kuni +0.032) ilma selge
suunata. **Signal Lifecycle staatus (ChatGPT kinnitatud):**

- **RS1 / RS_slope / RS_accel (vs SPY) = Rejected/Inconclusive** — praeguses kujul
  ei minda Locked Test'ile.
- **RS2 (vs sektor) = Deferred, mitte Rejected** — kontseptuaalselt eraldiseisev,
  seni testimata hüpotees. Ei ehitata praegu sektori-andmete infrastruktuuri ainult
  RS2 jaoks — kui Money Flow annab tulevikus signaali, mis vajab sektori konteksti
  (sektori tugevus, sektori-rotatsioon, universumi filtreerimine), tasub sektori-infra
  ehitada laiemal põhjendusel korraga, mitte RS2 jaoks eraldi.

### Uus Hypothesis Backlog kirje: RSI oversold reversal (erinev olemasolevast rsi_signal'ist)

ChatGPT tõi välja olulise eristuse: olemasolev `rsi_signal` (Trade Score v2
komponent) annab **rohkem punkte kõrgema RSI eest** (lineaarne "tugevuse" loogika).
`RSI < 30` on **vastupidine, klassikaline "oversold reversal" hüpotees** — kontseptuaalselt
erinev signaal samast alusnäitajast, mitte sama asi teises vormis.

```
Hüpotees: RSI_oversold (RSI < 30) ennustab positiivset triple-barrier R-multiple'i
Primary horizon: 10d või 20d (fikseeritakse enne testimist)
Test: Bull/Bear eraldi
Allikas: Theory-driven (klassikaline mean-reversion faktor kirjandusest)
Staatus: Candidate (registreeritud, testimata)
```

Testitakse eraldi signaalina Signal Lab'i kaudu, mitte C1-ga segatuna.

### Oluline piirang, mis jäi testimata: RS2 (aktsia vs SEKTOR)

Testisime ainult "aktsia vs SPY" kuju. ChatGPT algne hüpotees rõhutas spetsiifiliselt
"aktsia vs oma sektor" versiooni ("aktsia püsib tugevana, kui *tema sektor* on nõrk")
kui kontseptuaalselt kõige huvitavamat — see erineb põhimõtteliselt "aktsia vs kogu
turg" (RS1) versioonist, kuna RS1 segab kokku turu-beeta/sektori-efektid
aktsia-spetsiifilise tugevusega, mis võib seletada, miks tulemus oli müra. RS2 jäi
teostamata, kuna projektis on teadaolev sektori-andmete usaldusväärsuse probleem
(yfinance sektoripäring rate-limiteerub sageli, langeb "unknown" peale).

**ChatGPT otsus:** ära ehita sektori-infrastruktuuri praegu ainult RS2 jaoks. Liigu
edasi Money Flow juurde (RVOL, OBV slope, A/D slope — ei vaja sektori andmeid).
Kui Money Flow annab signaali, mis vajab sektori konteksti, ehitada sektori-infra
siis laiemal põhjendusel (RS2 + sektori tugevus + sektori-rotatsioon + universumi
filtreerimine korraga).

## 11. Cycle #2 Step 3: Money Flow (RVOL/OBV_slope/AD_slope) — tulemus ja järeldus

Testisime kolme Money Flow signaali dev-valimil (seed=42), sama metoodika ja
distsipliiniga (causal walk-forward IC, 80/20 split, primary lookback/horisont=20
päeva, secondary=10/40, Bull/Bear lõige algusest peale).

**RVOL (tänane Volume / 20-päevane libisev keskmine Volume):**

| Horisont | Train IC | Holdout IC |
|---|---|---|
| 10d | +0.0424 | +0.0407 |
| 20d (PRIMARY) | +0.0315 | +0.0277 |
| 40d | +0.0258 | +0.0228 |

Režiim (20d): Train Bull +0.0448, Train Bear −0.0871; Holdout Bull +0.0374,
Holdout Bear −0.0133 — sama märk mõlemas perioodis mõlemas režiimis.

**obv_slope:** Train Bull −0.0193, Train Bear +0.0994; Holdout Bull +0.0744,
Holdout Bear −0.0085 (märgi pöördumine mõlemas režiimis).

**ad_slope:** järjekindlalt negatiivne, tugevam Bull'is (Train Bull −0.0253, Holdout
Bull −0.0506; Bear nõrgem mõlemas perioodis).

### Rolling window diagnostika (6-kuu aknad)

**RVOL Bull** (5 järjestikust akent, n=1054-6734): **+0.048, +0.036, +0.049, +0.037,
+0.057** — kõik positiivsed, sarnase suurusjärguga, ilma ühegi märgi-pöördumiseta
kogu ~2.5-aastase perioodi vältel. Esimene signaal terves projektis, mis läbib
rolling window kontrolli täiesti puhtalt.

**RVOL Bear** (4 akent, n=275-1600): +0.046, −0.144, −0.037, −0.033 — kolm
järjestikust viimast akent negatiivsed, esimene (väikseim valim) erand. Mõõdukalt
toetab "negatiivne Bear'is" mustrit, vähem puhtalt kui Bull.

**obv_slope Bull:** −0.085, −0.049, −0.013, +0.015, +0.030 — **sujuv, järkjärguline
triiv** negatiivsest positiivseks (erinevalt RS-i erratiliselt hüplevast mustrist).
Ei ole müra, aga pole ka praegu stabiilne — sõltub, millist ajahetke vaadata.

**ad_slope:** nõrk, aga suund suhteliselt püsiv (negatiivne enamikus akendest
mõlemas režiimis).

### Ettepanek: uus pre-registered hüpotees "MF1"

```
Hüpotees MF1: RVOL ennustab positiivset R-multiple'i Bull-režiimis, ja selle
IC on kõrgem kui Bear-režiimis.
Regime: Bull = SPY Close ≥ SMA200, Bear = SPY Close < SMA200 (sama split, mis M1)
Signal: rvol (Volume / 20-päevane libisev keskmine Volume), muutmata kujul
Primary horizon: 20 päeva
Primary target: R-multiple
Primary test: Spearman IC
Expected result: IC_bull > 0 JA IC_bull > IC_bear
```

See on struktuurilt identne M1-ga (lihtsalt Bull/Bear vahetatud), ja esimene
kandidaat, mis võiks anda projekti **esimese Bull-režiimi signaali**, kui see Locked
Test'il kinnitub.

**Avatud küsimus:** kas MF1 on ChatGPT hinnangul piisavalt täpselt sõnastatud ja
rolling window'ga toetatud, et see Locked Test'ile saata (koos või ilma S1-taolise
"RVOL replication" lisahüpoteesita), või tasub enne veel midagi kontrollida (nt
ticker/sektori kontsentratsioon, nagu tegime C1 puhul)?

## 12. MF1 Locked Test + praktiline profiil

Külmutatud hüpotees (ChatGPT täpsustusega): **MF1a (primary)** — RVOL omab
positiivset ennustusvõimet Bull-režiimis (IC_bull > 0). **MF1b (secondary,
informatiivne, mitte kohustuslik)** — IC_bull > IC_bear. Testitud samal Locked Test
valimil (seed=123), mida kasutati M1/S1/C1 jaoks — legitiimne taaskasutus, kuna RVOL-i
pole sellel valimil kunagi varem uuritud.

### Locked Test tulemus

```
IC_bull = +0.0350 (n=24666)   IC_bear = -0.0399 (n=3073)

MF1a (PRIMARY): IC_bull > 0                    → CONFIRMED
MF1b (secondary): IC_bull > IC_bear            → CONFIRMED

Info-decay: 10d IC_bull=+0.0491, 20d +0.0350, 40d +0.0268 (sujuv, mitte juhuslik)
```

**Eriti tugev kooskõla:** dev-valimi holdout Bull IC (+0.0374) ja Locked Test Bull
IC (+0.0350) on peaaegu identsed — esimene kord projektis, kus eksploratiivne ja
kinnitav tulemus nii lähestikku klapivad.

### Praktiline profiil (monotoonsus + tickerite kontsentratsioon)

| Lõige | n (raw) | n (dedup) | Success rate | Lift | Mediaan MFE | Mediaan MAE | Mediaan R |
|---|---|---|---|---|---|---|---|
| All Bull | 24666 | – | 33.9% | 1.000x | +0.039 | −0.037 | +1.094 |
| Top 30% | 7400 | 3936 | 34.4% | 1.015x | +0.038 | −0.037 | +1.143 |
| Top 20% | 4934 | 3143 | 34.1% | 1.007x | +0.041 | −0.039 | +1.137 |
| Top 10% | 2467 | 1905 | 33.2% | 0.979x | +0.045 | −0.042 | +1.103 |
| **Top 5%** | 1234 | 1054 | **32.7%** | **0.964x** | +0.049 | −0.049 | +1.053 |

**Murettekitav muster:** lift **langeb** lõike kitsenedes (1.015x → 1.007x → 0.979x
→ 0.964x) — Top 5% on tegelikult **halvem** kui baseline. See on vastupidine sellele,
mida hea rank-signaal peaks näitama, ja vastupidine ka C1 mustrile (kus kõik neli
lõiget olid statistiliselt eristamatud, ilma selge langusega).

Kiire SE-kontroll: Top30% (n=7400) SE~0.55pp, Top5% (n=1234) SE~1.34pp; erinevus
(1.7pp) vs kombineeritud SE (~1.45pp) ≈ 1.2 standardviga — suund on järjekindel
nelja punkti lõikes, kuigi mitte tugevalt statistiliselt eristuv.

**Tõlgendus:** RVOL ja R-multiple seos pole tõenäoliselt puhtalt monotoonne —
võimalik "magus koht" (mõõdukalt kõrgenenud maht informatiivne, ekstreemsed
mahu-hüpped — nt earnings/uudiste päevad — käituvad teisiti, "osta kuulujutt, müü
uudis" dünaamika).

**Tickerite kontsentratsioon (Top20% Bull, deduplitseeritud):** 3143 setup'it, **287
unikaalset tickerit** 288-st, mediaan **11 setup't tickeri kohta**, top-10 osakaal
ainult **5.0%** — isegi laiemalt jaotunud kui C1. Edge ei ole üksikute nimede
artefakt.

### Kokkuvõttev järeldus

MF1 on **statistiliselt kinnitatud (IC replitseerus kolmel andmestikul: dev-train,
dev-holdout, Locked Test), aga praktiliselt nõrgem kui C1 üksinda** — parim lift
(~1.015x Top 30%) on väiksem kui C1 Bear'i 1.073x, ja monotoonsus puudub täielikult
(pigem vastupidine trend). **Ettepanek:** MF1 sobib pigem tulevase kombinatsiooni
komponendiks (nt koos registreeritud RSI-oversold hüpoteesiga) kui iseseisvaks
valikureegliks.

**Avatud küsimus:** kas ChatGPT hinnangul on see langev-lift muster piisav põhjus,
et mitte kiirustada MF1-t iseseisva Bull-mooduli signaalina kasutusele võtma (nagu
C1 on Bear-moodulina), või on siin midagi, mida tasuks täiendavalt diagnoosida (nt
kas madal lift Top5%-l on seotud konkreetsete kõrge-RVOL päevade tüübiga, nagu
earnings-reaktsioonid)?

## 13. MF1 outcome diagnostika: suund vs amplituud, ja detsiili murdepunkt

ChatGPT tõstatas §12 põhjal terava tähelepaneku: kuna nii mediaan MFE kui MAE
**kasvasid** RVOL lõike kitsenedes (Top30%→Top5%), samal ajal kui success rate
**langes**, võib RVOL ennustada pigem liikumise **amplituudi** ("midagi hakkab
juhtuma"), mitte **suunda**. Soovitas kontrollida IC(rvol,mfe)/IC(rvol,\|mae\|) ja
RVOL×ATR% interaktsiooni, enne RSI-oversold juurde liikumist.

### Esimene diagnostika (täisvahemiku Spearman IC)

```
IC(rvol, mfe)          = -0.0537
IC(rvol, |mae|)        = -0.1167
IC(rvol, mfe+|mae|)    = -0.1214
IC(rvol, r_multiple)   = +0.0350  (MF1 primary tulemus)
IC(rvol, success)      = +0.0286
```

**Üllatav, hüpoteesile vastupidine tulemus:** täisvahemiku IC oli **negatiivne**
MFE/MAE jaoks, mitte positiivne, nagu amplituudi-hüpotees ennustas. See viitas, et
täisvahemiku Spearman IC ja tippkvantiilide (§12) sisemine muster mõõdavad erinevat
asja — vajas täpsustavat detsiili-analüüsi.

**ATR% interaktsioon:** IC(rvol, r_multiple) kasvas ATR% tertsiilide lõikes (Low
+0.020 → Medium +0.028 → **High +0.046**) — RVOL ei kao kõrge-volatiilsuse sees,
mis kinnitab, et see kannab infot lisaks pelgale ATR tasemele.

### Täpsustav detsiili-analüüs (10 detsiili, D1=madalaim RVOL, D10=kõrgeim)

| Detsiil | RVOL vahemik | n | Mean MFE | Mean \|MAE\| | Mean R | Success rate |
|---|---|---|---|---|---|---|
| D1 | 0.00-0.40 | 2467 | **+0.174** | **0.101** | +1.617 | **0.272** |
| D2 | 0.40-0.56 | 2467 | +0.107 | 0.063 | +1.455 | 0.334 |
| D3 | 0.56-0.67 | 2466 | +0.072 | 0.054 | +1.359 | 0.326 |
| D4 | 0.67-0.77 | 2467 | +0.065 | 0.045 | +1.409 | 0.362 |
| D5 | 0.77-0.86 | 2466 | +0.065 | 0.046 | +1.353 | 0.350 |
| D6 | 0.86-0.97 | 2467 | +0.058 | 0.043 | +1.426 | 0.352 |
| D7 | 0.97-1.09 | 2466 | +0.058 | 0.043 | +1.396 | 0.360 |
| D8 | 1.09-1.29 | 2467 | +0.062 | 0.045 | +1.421 | 0.349 |
| D9 | 1.29-1.68 | 2466 | +0.077 | 0.050 | +1.465 | 0.350 |
| D10 | 1.68-19.81 | 2467 | +0.117 | 0.067 | +1.581 | 0.332 |

### Järeldus: U-kujuline MFE/MAE, aga erinev success rate muster

**MFE ja MAE moodustavad selge U-kõvera** — kõrgeimad mõlemas äärmuses (D1 ja D10),
madalaimad keskel (D6-D7). See seletab täielikult varasema "vastuolulise" täisvahemiku
negatiivse IC — Spearman korrelatsioon võtab arvesse D1→D7 langevat trendi, mis
domineerib arvutust ja varjab D8-D10 osalist taastumist.

**Success rate käitub teisiti:** **D1 on selgelt halvim** (0.272, madalaim kõigist)
— sobib kokku "õhukese kauplemise müra" hüpoteesiga (suured juhuslikud liikumised
mõlemas suunas, kaotused domineerivad). D2-D9 moodustavad suhteliselt lameda platoo
(~0.33-0.36). **D10 langeb kergelt platoo tipust tagasi** (0.332).

### Täpsustatud hüpotees Hypothesis Backlog'i jaoks

```
Hüpotees "RVOL_filter": RVOL peamine praktiline väärtus on likviidsuse FILTRINA
(väldi äärmiselt madalat RVOL-i, D1-tüüpi), mitte "chase kõrgeimat RVOL-i" reeglina.
Success rate on madalaim just kõige madalama RVOL-i detsiilis, mitte kõrgeima.
Staatus: Candidate (registreeritud, vajab täiendavat testimist teisel valimil)
```

**Avatud küsimus:** kas see täpsustus (RVOL kui alumise-otsa filter, mitte ülemise-otsa
ranking) muudab MF1 staatust või praktilist kasutusviisi Trade Engine'is, ja kas
tasub seda uut, täpsemat hüpoteesi eraldi testida enne RSI-oversold juurde
liikumist?

## 14. D1 diagnostika lõpptulemus: "Low Relative Participation" kinnitatud, fat-tail selgitatud

ChatGPT täpsustas §13 järel, et "Liquidity Filter" oli semantiliselt liiga tugev
väide (RVOL mõõdab suhtelist aktiivsust, mitte absoluutset likviidsust) ja nõudis
kahte piiratud diagnostikat enne mistahes uue hüpoteesi registreerimist: (1) kas D1
efekt püsib ADV20 likviidsuse tertsiilides, (2) kas D1 üllatavalt kõrge mean R
(1.617) tuleb fat-tail jaotusest.

### 1. D1 efekt ADV20 (likviidsuse) tertsiilides

| Tertsiil | D1 success | non-D1 success | Gap |
|---|---|---|---|
| Low_ADV | 0.300 (n=1615) | 0.331 (n=6888) | −0.031 |
| Medium_ADV | 0.262 (n=698) | 0.345 (n=7804) | −0.083 |
| High_ADV | 0.290 (n=238) | 0.340 (n=8265) | −0.050 |

**Gap on negatiivne kõigis kolmes tertsiilis, sh High_ADV** (kõige likviidsemad
suure-kapitalisatsiooniga aktsiad). **Kinnitatud: see ei ole absoluutse
ebalikviidsuse artefakt** — efekt on genuiinne "madala suhtelise osalemise" nähtus,
mitte lihtsalt "D1 = väikesed ebalikviidsed aktsiad".

### 2. D1 R-multiple jaotus (fat-tail kontroll)

| | Mean | Median | Trimmed(10%) | Winsorized(5%) | p95 |
|---|---|---|---|---|---|
| D1 | **1.652** | 0.911 | 1.125 | 1.271 | 4.083 |
| D2-D10 | 1.396 | 1.102 | 1.240 | 1.295 | 3.160 |

D1 mean-median lõhe (0.74) on palju suurem kui D2-D10 oma (0.294) — D1 p95 (4.08)
on selgelt paksem kui D2-D10 oma (3.16). **Pärast trimmimist/winsoriseerimist on D1
tegelikult marginaalselt HALVEM, mitte parem, kui D2-D10** (trimmed: 1.125 vs 1.240;
winsorized: 1.271 vs 1.295).

**Terminoloogia parandus (ChatGPT tähelepanek):** "D1 kõrge mean R on statistiline
artefakt" on liiga tugev väide — fat right tail on **reaalne** osa jaotusest, mitte
artefakt. Õigem sõnastus: **D1 kõrge keskmine R ei kirjelda tüüpilise setup'i
kvaliteeti, vaid on tugevalt mõjutatud haruldastest väga suurtest võitjatest.** See
tähendab, et Low Relative Participation on halb keskkond *tüüpilise* swing-trade
kandidaadi jaoks, kuid selle sees eksisteerib haruldane erandite klass väga suurte
tõusudega — mida ei tohiks tulevases mudelis kaotada jäiga välistusreegliga.

### Lõplik järeldus

**"Low Relative Participation" (D1) on halb keskkond tüüpilise kandidaadi jaoks**,
mis püsib kõigil likviidsuse tasemetel — madal võiduprotsent, ja riskiga kohandatuna
(trimmed/winsorized) pigem kergelt kehvem kui tavaline tsoon. Samas ei tohiks seda
implementeerida jäiga reeglina (`if RVOL < 0.4: reject`) — pigem tulevikus
kontekstuaalse negatiivse kaaluna (`low_relative_participation = true`), mida mõni
teine tugev signaal saab kompenseerida, et mitte kaotada harva esinevaid suure
tõusupotentsiaaliga erandeid.

**MF1 staatus jääb muutumatuks** (Validated Bull-regime Informational Feature).
**"Low Relative Participation" = Candidate Context Feature** (mitte Validated —
hüpotees sündis Locked Test andmete uurimisest ja vajab tulevikus oma
confirmatory valimit; ChatGPT soovitab mitte kulutada uut Locked Test valimit
selle kohese kinnitamise peale praeguses faasis).

## 15. RSI-O1: RSI(14)<30 oversold reversal — Signal Lab

Järgmine samm, ChatGPT täpsustusega registreeritud enne testimist:

```
Hüpotees RSI-O1: RSI(14) < 30 seisund ennustab paremat järgneva swing-trade
outcome'i kui RSI >= 30, mõõdetuna 20-päeva triple-barrier R-multiple ja
success rate'iga.
Primary horizon: 20 päeva (mitte 10 — võrreldavuse pärast C1/MF1-ga)
Secondary horizons: 10 ja 40 päeva
Analüüs: Bull ja Bear režiimis eraldi
Primary metrics: Δ success rate, Δ mediaan R-multiple (mitte Spearman IC, kuna
  signaal on binaarne, mitte pidev)
Lisamõõdikud: MFE/MAE profiil mõlema grupi jaoks
Oluline: testitakse SEISUNDIT (RSI<30 vs RSI>=30), mitte olemasolevat
  rsi_signal skoori (mis on juba olemas Trade Score v2-s, aga testib
  vastupidist majanduslikku hüpoteesi — kõrgem RSI = tugevus, mitte
  mean-reversion)
```

## 16. RSI-O1 tulemus: klassikaline oversold reversal REJECTED

Testisime RSI-O1 hüpoteesi (RSI(14)<30 vs ≥30) dev-valimil (seed=42), primary
horizon=20d, secondary=10d/40d, Bull/Bear eraldi, primary metrikad success rate
delta ja median R delta (mitte IC, kuna signaal on binaarne).

### Tulemus: kõik kuus horisont×režiim kombinatsiooni negatiivsed

| Horisont | Režiim | Success delta | Median R delta |
|---|---|---|---|
| 10d | Bull | −0.034 | −0.121 |
| 10d | Bear | −0.023 | −0.080 |
| **20d (PRIMARY)** | **Bull** | **−0.031** | **−0.076** |
| **20d (PRIMARY)** | **Bear** | **−0.041** | **−0.161** |
| 40d | Bull | −0.007 | −0.012 |
| 40d | Bear | −0.055 | −0.165 |

**Kõik kuus kombinatsiooni näitavad sama suunda** — RSI<30 ennustab **halvemat**,
mitte paremat tulemust igal horisondil ja mõlemas režiimis. See on otsene
klassikalise "oversold reversal" hüpoteesi ümberlükkamine.

**Metodoloogiline täpsustus (ChatGPT):** need kuus rakku **ei ole kuus sõltumatut
kinnitust** — 10d/20d/40d outcome-aknad kattuvad ajaliselt ja kasutavad samu
aktsiaid/turusündmusi, ning Bull/Bear lõiked ei anna ajalist sõltumatust. Täpsem
sõnastus: *primary hüpotees ebaõnnestus vastupidises suunas, ja kõik secondary
horisont/režiimi tulemused on selle ebaõnnestumisega suunaliselt kooskõlas.*
Rolling window diagnostikat pole vaja, mitte sellepärast, et oleks "kuus kinnitust",
vaid kuna primary test juba ebaõnnestus selgelt vastupidises suunas.

### Huvitav paralleel D1 (Low Relative Participation) nähtusega

MFE on ülemüüdud grupis dramaatiliselt kõrgem (20d Bull: 0.446 vs 0.071 — ~6x),
MAE samuti kõrgem (0.107 vs 0.056) — täpselt sama muster, mida nägime D1 juures
(§14): äärmuslik seisund → suuremad liikumised mõlemas suunas, aga **halvem, mitte
parem** tüüpiline (mediaan) tulemus.

### Uus backlog-kirje

```
Nähtus "Extreme State Instability": äärmuslikud/haruldased seisundid (nii väga
madal RVOL kui väga madal RSI) näitavad korduvalt sama mustrit — kõrgenenud
MFE/MAE (suurem variatiivsus), aga halvem, mitte parem, mediaan-tulemus ja
success rate. Võimalik üldisem turukäitumise nähtus, väärt meelespidamist
tulevaste "äärmuslik väärtus = hea signaal" tüüpi hüpoteeside testimisel.
Staatus: Observation (mustri märkus, mitte veel iseseisev testitav hüpotees)
```

### Signal Lifecycle uuendus

**RSI-O1 (RSI<30 klassikaline oversold reversal) = Rejected.** Ei minda Locked
Test'ile. Olemasolev `rsi_signal` (Trade Score v2, tugevuse-hüpotees) jääb
puutumata — see on endiselt eraldi, juba varem osaliselt uuritud signaal.

**Avatud küsimus:** kas ChatGPT nõustub, et see tulemus on piisavalt selge
tagasilükkamiseks ilma rolling window kontrollita, ja mis peaks olema järgmine
samm — kas Volatility Compression/Expansion nähtuse juurde liikumine (ChatGPT
varem mainitud), või midagi muud?

## 17. VC1 tulemus: compression ennustab amplituudi, mitte suunda — nagu klassikaline teooria ütleb

Testisime VC1 hüpoteesi (bottom 20% compression_pct vs ülejäänud) dev-valimil,
primary horizon=20d, secondary=10d, Bull/Bear eraldi, mõõtes nii suunda
(success rate delta, median R delta) kui amplituudi (MFE, MAE).

### Tulemus

| Horisont | Režiim | Success delta | Median R delta | MFE (compressed vs mitte) |
|---|---|---|---|---|
| 10d | Bull | −0.007 | −0.069 | 0.119 vs 0.069 |
| 10d | Bear | −0.032 | −0.031 | 0.112 vs 0.083 |
| **20d (PRIMARY)** | **Bull** | −0.007 | −0.060 | **0.164 vs 0.075** |
| **20d (PRIMARY)** | **Bear** | −0.039 | −0.088 | 0.127 vs 0.095 |

Kõik neli kombinatsiooni näitavad madalamat success rate'i ja median R-i
kompressiooni-seisundis, samal ajal kui MFE on 20d Bull'is ligi **2x kõrgem**
(0.164 vs 0.075).

### Miks see EI OLE sama järeldus, mis RSI-O1 puhul

RSI-O1 oli pre-registreeritud **otsese suuna-hüpoteesina** ("RSI<30 ennustab
tõusu") ja ebaõnnestus selgelt — suund oli vale suunas. **VC1 oli teadlikult
disainitud testima mõlemat (suund JA amplituud) eraldi, ilma eeldamata, et
suund üksi peaks töötama.** Klassikaline "Bollinger Squeeze" teooria ennustab
otseselt, et kompressioon ennustab **suurt liikumist**, mitte **liikumise
suunda** — suuna peab määrama alles aktiveerimis-trigger (breakout, mahu
kinnitus). Selle vaatenurga alt käitub VC1 tulemus **täpselt nii, nagu teooria
ennustaks**: amplituud (MFE) kahekordistus, suund üksi ei paranenud (mida
polnudki oodata ilma triggerita).

**See õigustab liikumist VC2 (compression + aktiveerimis-trigger) juurde, mitte
kogu nähtuse tagasilükkamist**, erinevalt RSI-O1-st.

### Täpsustus: |MAE| kontroll (ChatGPT nõutud, olemasolevatest andmetest)

ChatGPT tähelepanek: MFE üksi mõõdab ainult ülespoole potentsiaali, mitte
sümmeetrilist amplituudi. Juba testitud andmetest (§17 esialgne tabel) saab
|MAE| kõrvutada:

| Horisont/Režiim | MFE (compr vs mitte) | \|MAE\| (compr vs mitte) |
|---|---|---|
| 10d Bull | 0.119 vs 0.069 (+72%) | 0.055 vs 0.054 (+4%) |
| 10d Bear | 0.112 vs 0.083 (+35%) | 0.070 vs 0.059 (+20%) |
| 20d Bull | 0.164 vs 0.075 (+113%) | 0.061 vs 0.057 (+6%) |
| 20d Bear | 0.127 vs 0.095 (+34%) | 0.079 vs 0.065 (+22%) |

**|MAE| tõuseb kõigil neljal juhul** — kinnitab, et tegu on tõesti
amplituudi-nähtusega, mitte ainult MFE-spetsiifilise artefaktiga. **Aga
asümmeetria on soodne projekti eesmärgi jaoks:** 20d Bull'is kasvab MFE 113%,
|MAE| ainult 6% — kompressioon ennustab ebaproportsionaalselt rohkem ülespoole
potentsiaali, tagasihoidliku downside kasvuga.

### Kolmas kinnitus "Extreme State Instability" mustrile

See on nüüd kolmas kord (D1 madal RVOL, RSI oversold, nüüd compression), kus
äärmuslik/haruldane seisund → suurem amplituud, aga halvem tüüpiline (mediaan)
tulemus. Jääb **Observation** staatusesse — praegune väärtus on metodoloogiline
õppetund (äärmuslikke seisundeid tuleb testida eraldi amplituudi ja suuna
suhtes), mitte veel iseseisev testitav hüpotees.

## 18. VC2-PB: Compression + Bullish Breakout — pre-registered hüpotees

ChatGPT valis **price breakout**, mitte RVOL, esimeseks aktiveerimis-triggeriks
(puhtam katse — RVOL on juba valideeritud Bull-informational feature, mistõttu
compression+RVOL test ei eristaks selgelt, kust edge tuleb).

```
Hüpotees VC2-PB: pärast volatility compression seisundit toimuv bullish
breakout (Close > eelneva 20-päeva High) identifitseerib setup'id, millel on
parem 20-päeva triple-barrier outcome kui compression-seisundil ilma
breakout'ita.

Breakout definitsioon (külmutatud, ei testita alternatiive): Close > prior
20-day High (tänane päev rolling high arvutusest välja jäetud)

Primary horizon: 20 päeva
Secondary horizon: 10 päeva
Primary metrics: success-rate delta, median R delta
Secondary: MFE, |MAE|, deduplitseeritud setup arv, coverage

Testistruktuur: 2×2 seisundi võrdlus
                    No breakout    Bullish breakout
No compression           A               B
Compression               C               D

Põhiküsimused:
  D − C > 0? (breakout aitab compression-seisundis)
  D − B > 0? (compression parandab breakout'i kvaliteeti)
```

See eristab tõelist interaktsiooni geneerilisest breakout-efektist — mitte
ainult "kas compression+breakout on parem kui kogu populatsioon".

## 19. VC2-PB tulemus: REJECTED — breakout ei paranda, tõenäoliselt hilineb

Testisime VC2-PB 2×2 disainiga dev-valimil, primary 20d, secondary 10d.

### Bull-režiim (97% andmestikust): järjekindel tagasilükkamine

| Horisont | D−C (breakout aitab compression'is?) | D−B (compression parandab breakout'i?) |
|---|---|---|
| 10d Bull | −0.066 | −0.042 |
| 20d Bull | −0.075 | −0.036 |

Mõlemad võtmeküsimused on **negatiivsed mõlemal horisondil** — puhas, konsistentne
tagasilükkamine seal, kus valdav enamus andmetest asub.

### Bear'i "positiivne" tulemus on müra (n=5)

Bear'i compression/breakout rakk sisaldab 20d horisondil ainult **n=5**
vaatlust — success rate 0.600 tähendab 3 võitu 5-st, statistiliselt
mittetõlgendatav. Toon selle eraldi esile, et vältida ekslikku üldistust.

### Kaks huvitavat lisaleidu

1. **Breakout ise (isegi ilma compression'ita) on kehvem kui mitte-breakout**:
   `no_compression` real 20d Bull — no_breakout success=0.331 vs
   breakout=0.282 (median R 1.059 vs 0.870). See on laiem muster kui ainult
   VC2-PB — sarnaneb varasemate leidudega (Trend, kõrge RSI, kõrge RVOL), kus
   juba-laienenud/kõrgele-jõudnud seisundite "jälitamine" alaperformib selles
   andmestikus.

2. **MFE kahaneb dramaatiliselt pärast breakout'i kinnitust**: compression/
   no_breakout MFE=0.162 (20d) vs compression/breakout MFE=**0.033** — 5x
   väiksem. **ChatGPT täpsustus:** "hiline entry" tõlgendus on usutav, aga
   praegune ristlõikeline 2×2 disain **ei tõesta** seda otseselt — selleks
   oleks vaja event-path analüüsi (T0 compression → T+n breakout → post-breakout
   outcome samal aktsial), mida praegu ei tehta (huvitav diagnostika, aga ei
   blokeeri järgmist signaalitesti).

### Signal Lifecycle uuendus (ChatGPT täpsustatud)

Oluline eristus kahe väite vahel: (1) "compression + activation" idee
ebaõnnestus — **seda §19 ei näita**; (2) "compression + Close>eelneva 20d
High" ebaõnnestus — **seda §19 näitab selgelt**. Seetõttu:

- **VC1 Compression State = Promising Amplitude/Instability State** (mitte
  lihtsalt Observation — see on juba kaks korda kinnitatud amplituudi-nähtus)
- **VC2-PB = Rejected Activation Mechanism** (konkreetne breakout-definitsioon,
  mitte kogu VC uurimisrada)
- **VC uurimisrada = Open**, üks viimane teooria-põhine aktiveerimis-test lubatud

Ei liiguta alternatiivsete price-breakout definitsioonide juurde (10-päeva
high, ATR-breakout, Bollinger-breakout) — see looks parameetriruumi
kaevandamise riski ilma positiivse empiirilise põhjuseta.

## 20. VC3-RVOL: Compression + RVOL Activation — pre-registered hüpotees

Kuna price-breakout aktiveerimise mehhanism ebaõnnestus, aga RVOL-il on juba
sõltumatu empiiriline alus (MF1 Locked Test kinnitatud, IC replitseerus
dev-holdout ja Locked Test vahel), on majanduslikult huvitavam küsimus:

> Kas compression state vajab lihtsalt aktiivsuse suurenemist, mitte juba
> toimunud hinnamurde kinnitamist?

```
Hüpotees VC3-RVOL:
Külmutatud aktiveerimisreegel: RVOL_activation = tänane RVOL > 1.0
  (lihtne, majanduslikult selge — tänane maht ületab 20-päeva keskmist;
  MITTE ekstreemne lävend, kuna varasem detsiili-analüüs näitas, et
  kõrgeim RVOL pole monotoonselt parim)

2×2 disain:
                    RVOL ≤ 1        RVOL > 1
No compression         A               B
Compression            C               D

Primary regime: Bull (VC1 kõige huvitavam asümmeetria oli seal, MF1 on
  Bull-spetsiifiline)
Primary horizon: 20 päeva
Secondary horizon: 10 päeva
Primary metrics: success-rate delta, median R delta

ASÜMMEETRILINE pre-registreering (VC2 õppetund — kaks küsimust vastavad
erinevatele majanduslikele hüpoteesidele, ei nõuta mõlema positiivsust):
  PRIMARY CLAIM: D > C
    "RVOL activation parandab compression state'i directional outcome'i"
  SECONDARY (incremental) CLAIM: D > B
    "Compression lisab väärtust RVOL activation'ile"

Stopping rule: see on VC uurimisraja viimane test praeguses tsüklis.
  Kui positiivne → praktiline profiil + võimalik Bull-moodul.
  Kui negatiivne → VC rada Deferred/Archived, liigume edasi.
```

## 21. VC3-RVOL tulemus: PRIMARY PASS, aga rolling window näitab segast pilti

### Stopping-rule test (20d, Bull, dev-valim)

```
PRIMARY (D>C):   success_delta=+0.022  median_R_delta=+0.154  -> PASS
SECONDARY (D>B): success_delta=-0.001  median_R_delta=+0.048  -> FAIL (ei nõuta)
```

**D-rakk (compression+RVOL) näitas silmapaistvat profiili** kõigi nelja mõõdiku
lõikes:

| Rakk (20d Bull) | Success | Median R | MFE | \|MAE\| |
|---|---|---|---|---|
| A (ei compr., rvol≤1) | 0.321 | 1.026 | 0.076 | 0.059 |
| B (ei compr., rvol>1) | 0.337 | 1.073 | 0.074 | 0.055 |
| C (compr., rvol≤1) | 0.314 | 0.967 | 0.144 | 0.063 |
| **D (compr., rvol>1)** | **0.336** | **1.121** | **0.230** | **0.053** |

D-rakk on parim kolmes neljast mõõdikust, **madalaim** |MAE| kõigist — erinevalt
kõigist varasematest "äärmuslik seisund" leidudest (D1, RSI oversold, compression
üksi), kus suurem MFE käis alati käsikäes suurema MAE-ga.

### Rolling window (6-kuu aknad) — segane, mitte puhas kinnitus

| Aken | D−C success | D−C median R | n (C/D) |
|---|---|---|---|
| 2024-05 | +0.003 | +0.046 | 158/52 |
| 2024-11 | −0.032 | −0.058 | 881/252 |
| 2025-05 | +0.045 | −0.073 | 653/144 |
| **2025-11** | +0.040 | **+0.405** | 1193/391 |
| 2026-05 | +0.017 | −0.021 | 825/267 |
| 2026-11 | +0.103 | −0.426 | 65/21 (liiga väike valim) |

**Success rate:** 4/6 akent nõrgalt positiivsed, üks negatiivne — tagasihoidlik,
kuid mitte täiesti vastuoluline muster.

**Median R on murettekitavam:** ainult **üks** aken (2025-11) näitab suurt
positiivset nihet (+0.405), kolm teist akent on negatiivsed. **Agregaat-tulemuse
(+0.154) domineerib peaaegu täielikult see üks ajaaken.**

**Tähelepanek:** 2025-11 aken kattub tõenäoliselt sama kõrgenenud-volatiilsuse
perioodiga, mis Cycle #1 alguses tuvastati (dev-holdout'i režiimi-nihe,
Bull_High ilmumine alles hilisemas perioodis) — võimalik, et VC3 "primary pass"
kajastab osaliselt sama perioodi-spetsiifilist nähtust, mis aitas ka Momentum/RSI
puhul, mitte täiesti sõltumatut efekti.

### Ei ole puhas kummagi interpretatsioonijuhendi stsenaarium

See pole Momentum'i sujuv, järkjärguline muster, aga pole ka RS_slope'i täiesti
kaootiline märgi-hüplemine — vahepealne olukord: nõrk, ebajärjekindel
success-rate paranemine + median R, mis on suuresti ühe ajaakna vedatud.

**Avatud küsimus:** kas ChatGPT hinnangul väärib see täiendavat kontrolli (nt kas
2025-11 aken kattub teadaoleva režiimi-nihkega, mis mõjutas ka Momentum/RSI
tulemusi), või tuleks VC3 sellegipoolest ettevaatlikult Locked Test'ile saata,
arvestades, et formaalne primary kriteerium agregaadi tasemel täideti?

## 22. VC3-RVOL Locked Test + praktiline profiil: REPLICATED

### Locked Test tulemus (seed=123, sama valim mis M1/S1/C1/MF1, esimene kasutus VC3 jaoks)

```
D-C success delta: +0.005 (positive)
D-C median R delta: +0.077 (positive)
VERDICT: REPLICATED
```

**Tulemus vastab peaaegu täpselt ChatGPT eelnevalt registreeritud (enne testi
kirja pandud) ootusele:**

| | Dev (algne) | Locked Test | Pre-test ootus |
|---|---|---|---|
| Success delta (D−C) | +0.022 | +0.005 | "nõrgalt replitseerub" ✓ |
| Median R delta (D−C) | +0.154 | +0.077 | "ei replitseeru täies suuruses, väiksem positiivne pole ebaõnnestumine" ✓ |

See on metodoloogiliselt eriti veenev — ennustati tulemuse **kuju** enne testimist,
mitte ainult suunda, ja see kattus.

D-raku sekundaarne profiil (Locked Test): MFE=0.090, |MAE|=0.050 (~1.8x suhe,
säilitab soodsa asümmeetria, kuigi absoluutväärtused väiksemad kui dev-valimis —
kooskõlas ootusega, et efekt oli osaliselt ajaliselt kontsentreeritud).

### Praktiline profiil

**Monotoonsus D-raku sees (lame, mitte tõusev):**

| Lõige | Success rate | Median R |
|---|---|---|
| All D | 0.333 | 1.133 |
| Top 50% RVOL | 0.311 | 1.085 |
| Top 30% RVOL | 0.317 | 1.109 |
| Top 20% RVOL | 0.325 | 1.085 |

Kõrgem RVOL D-raku sees ei paranda tulemust edasi — kordab MF1 detsiili-analüüsi
mustrit (kõrgeim RVOL pole monotoonselt parim). Efekt tuleb läve ületamisest
(RVOL>1), mitte "mida rohkem, seda parem" loogikast — täpselt nagu binaarse,
mitte ekstreemse läve valik ette nägi.

**Tickerite kontsentratsioon:** 277/287 tickerit panustasid, mediaan 3 setup't
tickeri kohta, max 10, **top-10 osakaal 8.3%** — laialt jaotunud, sama muster
mis C1 ja MF1 puhul.

**Coverage:** **482 deduplitseeritud setup't aastas** kogu universumi peale —
tunduvalt sagedasem kui C1 (~107/aastas Bear-spetsiifiliselt).

### Kolm valideeritud ehituskivi

| Komponent | Režiim | Iseloom |
|---|---|---|
| C1 (Support + Bear-Momentum) | Bear | Harvem, tugevam edge (~320 setup't/3a) |
| MF1 (RVOL) | Bull | Laiaulatuslik konteksttunnus, tagasihoidlik lift |
| **VC3-RVOL** (Compression + RVOL activation) | Bull | Sagedasem signaal (~482 setup't/a), mõõdukas edge |

See on täpselt režiimipõhine struktuur, mida ChatGPT terve tsükli jooksul ette
nägi: Bear → C1; Bull → MF1 kontekst + VC3 aktiveerimine.

**Avatud küsimus:** kas ChatGPT hinnangul on see piisav, et lugeda VC-uurimisrada
edukalt lõpetatuks (VC3 = Validated Combination Candidate, sarnaselt C1-ga), ja
milline peaks olema järgmine samm — kas registreeritud RSI-oversold+RVOL
kombinatsiooni test (mis jäi varem ootele), Bull-mooduli kokkupanemine
olemasolevatest valideeritud tükkidest, või hoopis midagi muud?

## 23. Cycle #3: Regime-Aware Model Assembly — baseline portfelli backtest

ChatGPT soovitas liikuda signaali-avastamiselt süsteemi ehitamisele: külmutatud
arhitektuur (Bear=C1 kõrge-tsoon, Bull=VC3-RVOL D-rakk, MF1/RVOL ainult
tie-break'ina), täisulatuslik portfelli simulatsioon (positsioonipiirang,
tehingukulud, kapatsiteedikonkurents), ilma ühegi tuunimiseta.

### Baseline tulemus (dev-valim, seed=42, 3 aastat)

```
Initial capital:      $100,000
Final equity:          $107,043
CAGR:                   +3.19%
Max drawdown:           -38.49%
Sharpe:                 0.25
Sortino:                0.59
Trade count:            1759
Trades per year:        812.2
Profit factor:          1.06
Expectancy/trade:       $8.22
Regime exposure:        Bull 88.4%, Bear 11.6%
Max same-day entries:   17
```

**Aus esmahinnang:** tagasihoidlik CAGR koos väga suure drawdown'iga — Sharpe 0.25
ja profit factor 1.06 näitavad, et kolme valideeritud signaali kombinatsioon EI
anna automaatselt tugevat portfelli-tasandi tulemust, kui rakendada realistlikke
tehingukulusid, positsioonipiiranguid ja tegelikku kauplemismehaanikat.

### Diagnostika: kolm konkreetset probleemi

**1. Tehingukulud sõid 67.8% toorest kasumist**

```
Gross P&L (enne kulusid):  $21,860
Kogu tehingukulu:           $14,816  (sisenemine + väljumine)
Net P&L (pärast kulusid):   $7,043
```

Kauplemissagedus (812 tehingut/aastas) on edge'i suurusega võrreldes liiga kõrge
— süsteem genereerib liiga palju väikese-edge'iga tehinguid, mille kulud
järjekindlalt ära söövad.

**2. C1 (Bear) on tehingu kohta ~7.6x kasumlikum kui VC3 (Bull)**

| Režiim | Tehinguid | Kokku P&L | Keskmine/tehing | Win rate |
|---|---|---|---|---|
| Bear (C1) | 204 | $3,528 | **$17.30** | 35.8% |
| Bull (VC3) | 1555 | $3,515 | **$2.26** | 35.9% |

Peaaegu sama win rate mõlemal, aga C1 tehingu väärtus on palju kõrgem — Bull-poole
suur tehingumaht lahjendab portfelli tervikuna, kuigi kummagi komponendi
kogupanus on peaaegu võrdne. Kinnitab portfelli tasandil varasemat leidu (C1
praktiline lift 1.073x oli tugevam kui VC3 tagasihoidlikum edge).

**3. Drawdown on korreleeritud makrosündmuse, mitte hajutatud riski tulemus**

```
Peak:   2024-05-09  ($99,930)
Trough: 2025-04-11  ($61,468)
Kestus: 337 kalendripäeva
Selle akna tehingud: 837, win rate 32.02% (halvem kui üldine 36%)
Max samal päeval sisenemisi: 17
Clustering-päevad (≥10 sisenemist): 2024-05-09 (14), 2024-11-06 (11),
  2025-04-03 (14), 2025-04-04 (17), 2025-04-07 (14), 2025-04-10 (10)
```

**Otsing kinnitas:** 2025-04-03 kuni 2025-04-10 nädal langeb täpselt kokku
2025. aasta aprilli "Liberation Day" tariifikriisiga. 2. aprillil 2025
väljakuulutatud laiaulatuslikud tariifid vallandasid globaalse paanikamüügi —
suurima turulanguse alates 2020. aasta COVID-kriisist — ning esimese kahe
päevaga kadus üle 6.6 triljoni dollari turuväärtust (suurim kahepäevane kaotus
finantsajaloos). VIX hüppas mitmel intensiivsel päeval tasemest ~30 keskele
50ndatesse (kõrgeim tase alates 2020. aastast), S&P 500 langes ligi 10.5% kahe
kauplemispäeva jooksul, enne kui 9. aprillil väljakuulutatud 90-päevane
tariifide peatamine vallandas tugeva ralli (S&P +9.5% ühel päeval — ajaloo
kümnes parim päev). (Allikad: Cboe Index Insights, St. Louis Fed, Wikipedia
"2025 stock market crash", USI Consulting Group Market Volatility Update.)

**Tõlgendus:** drawdown polnud juhuslik korrelatsiooniartefakt, vaid täpne
reaktsioon ühele ajaloo erakordsemale (99. protsentiili) makrošokile —
korduva/tavapärase nähtuse asemel. Samas paljastab see reaalse ja püsiva
haavatavuse **mehhanismi**: compression+RVOL signaal käivitub just laiapõhjalise
volatiilsuse plahvatuse hetkedel paljudes aktsiates korraga — signaali
loomupärane omadus, mitte viga, mis vajab eksplitsiitset kaitset (nt päevane
uute-positsioonide lagi), mitte ainult üldist positsioonide-arvu piirangut.

**Avatud küsimus:** kuidas ChatGPT hinnangul edasi minna — kas (a) lisada päevane
uute-positsioonide lagi ja tehingusageduse/positsiooni-suuruse kohandus enne
järgmist testi, (b) testida sama arhitektuuri erineval ajaperioodil (nt
väljaspool 2025 aprilli šokki) drawdown'i tavapärasuse hindamiseks, või (c)
midagi muud?

## 24. Avatud küsimused järgmiseks etapiks

1. Kas C1 "Candidate → Core" ülendamiseks tuleks oodata reaalset uut turutsüklit
   (ajaline sõltumatus), või on olemas mõistlik proxy (nt eraldi test spetsiifiliselt
   varasemal, veel kasutamata ajaperioodil samadel tickeritel, isegi kui see pole
   "tulevik")?
2. Kas järgmisena tasub uurida **Money Flow** (RVOL, OBV, A/D) ja **Relative Strength**
   (RS vs SPY/sektor) nähtusi samas raamistikus, või kõigepealt proovida C1 valemit
   ise täiustada (nt kaalude tuunimine, mitte lihtne z-score liitmine) enne uute
   nähtuste lisamist?
3. Kas praegune ~320 setup'it/3a (Bear-spetsiifiline C1 tipp-grupp) on piisav
   sagedus praktilise swing trade süsteemi jaoks, või on see liiga harv, et olla
   iseseisvalt kasulik — s.t kas peaksime ootama mitme väikese, erineva-režiimi
   signaali kuhjumist, enne kui süsteem annab piisavalt regulaarseid kandidaate?
