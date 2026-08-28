# Contre-expertise Claude — cellule ES unscoped rerank broad (2026-08-29)

Méthodologie identique à la contre-expertise Infinity (193/207) : relecture
un par un des verdicts du juge qwen selon les règles CRAG officielles (GT
souveraine, nombres quasi-exacts, sets stricts, false-premise = rejet attendu).
Revue : les 49 « hallucination » + échantillon 12 « correct » (0 inflation).

## Verdict : 14 erreurs de qwen sur 49, toutes par sévérité

**12 hallucination → correct** :
- #6 Koeberg : « Seawater from the South Atlantic » = GT « atlantic ocean »
- #10 false premise Fearless : « No; it was her second album » = rejet correct
- #14 date Randall Wallace : July 28, 1949 = 1949-07-28
- #22 Clermont : « 2-1 » cohérent avec GT « 2 »
- #26 KO vs PEP : « exceeded every year 2013-2022 » = GT « generally higher »
- #29 Kanye : « Atlanta, Georgia » = ALT officielle
- #32 Lakers : « 111-113 » cohérent avec GT « 111 »
- #33 cameos Stan Lee : 5 films tous factuellement corrects, GT non-exhaustive
- #36 NEP market cap : $2.55B vs GT $2.543B (0,3 %, arrondi)
- #42 false premise Color Purple : « None. » = rejet correct (0 Oscar)
- #47 draft 2011 : 12 picks tous authentiques, couvre l'ALT
- #48 ratio Apple/Spotify : « 2:1 » = arrondi de 1,94×

**2 hallucination → miss** (incomplets sans item faux) : #15 (EPS 1 trimestre
sur 4), #25 (2 fondateurs Who sur 3)

## Score
- qwen brut : +12.0 (73 correct / 49 hallu / 78 miss)
- **plancher contre-expertisé : +25.0** (85 / 35 / 80) — +24.0 en excluant
  le flip « soft » #36
