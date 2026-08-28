# CRAG — rapport de scoring

- Mode : corpus entier (NON comparable au leaderboard)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **12.0** | 36.5% | 24.5% | 39.0% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 7 | 30 | 11 |
| movie | 19 | 10 | 16 |
| music | 11 | 9 | 5 |
| open | 16 | 11 | 8 |
| sports | 20 | 18 | 9 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 11 | 7 | 2 |
| comparison | 6 | 14 | 1 |
| false_premise | 1 | 12 | 4 |
| multi-hop | 6 | 3 | 3 |
| post-processing | 3 | 2 | 4 |
| set | 7 | 6 | 10 |
| simple | 20 | 20 | 13 |
| simple_w_condition | 19 | 14 | 12 |

