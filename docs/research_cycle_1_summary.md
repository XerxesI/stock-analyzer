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

## 9. Avatud küsimused järgmiseks etapiks

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
