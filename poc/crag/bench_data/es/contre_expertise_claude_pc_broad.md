# Contre-expertise Claude — cellule ES parent-child broad (2026-08-29)

Même méthodologie que les contre-expertises Infinity et ES baseline : relecture
des 46 « hallucination » selon les règles CRAG officielles + 8 « correct »
échantillonnés (8/8 authentiques, 0 inflation — 3e vérification convergente).

## 15 erreurs de qwen sur 46, toutes par sévérité

**12 hallucination → correct** : #7 rejet fausse prémisse Fearless, #16 score
2-1 vs GT 2, #18 13.5% vs GT ~13% (soft), #21 KO>PEP cohérent, #26 Kanye=ALT,
#29 co-headliners Eminem tous authentiques (incl. Jay-Z 2010), #31 cameos Stan
Lee valides, #32 déf. treasury yield (= jugée CORRECTE au baseline par le même
juge — incohérence qwen), #35 2.55B vs 2.543B (0.3%, soft), #41 rejet Color
Purple « None. », #44 draft 2011 tous authentiques, #45 ratio 2:1 ≈ 1.94.

**3 hallucination → miss** (incomplets sans item faux) : #3 Southern Africa
5/13 pays tous corrects, #20 fondateurs Who 2/3, #24 Boise 208 sans 986.

## Score
- qwen brut : **+14.0** (74 correct / 46 hallu / 80 miss)
- **plancher contre-expertisé : +27.5** (86 / 31 / 83) — +25.5 en excluant
  les 2 flips soft (#18, #35)

## Tableau final des planchers de la campagne
| Config | qwen | plancher Claude |
|---|---|---|
| Infinity unscoped rerank | +9.5 | +18.5 |
| ES baseline broad | +12.0 | +24.0/+25.0 |
| **ES parent-child broad** | **+14.0** | **+25.5/+27.5** |
(vainqueur KDD Cup 2024, GPT-4 judge : +28.4 — avec LoRA fine-tuné + DBs
spécialisées ; nous : RAG générique sans accès internet)
