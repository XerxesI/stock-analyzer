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

## 12. Avatud küsimused järgmiseks etapiks

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
