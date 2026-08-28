# CRAG — rapport de scoring

- Mode : corpus entier (NON comparable au leaderboard)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **14.0** | 37.0% | 23.0% | 40.0% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 7 | 29 | 12 |
| movie | 20 | 12 | 13 |
| music | 10 | 7 | 8 |
| open | 16 | 12 | 7 |
| sports | 21 | 20 | 6 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 10 | 6 | 4 |
| comparison | 7 | 13 | 1 |
| false_premise | 1 | 12 | 4 |
| multi-hop | 5 | 5 | 2 |
| post-processing | 3 | 2 | 4 |
| set | 7 | 7 | 9 |
| simple | 22 | 18 | 13 |
| simple_w_condition | 19 | 17 | 9 |

