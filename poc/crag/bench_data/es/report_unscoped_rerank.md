# CRAG — rapport de scoring

- Mode : corpus entier (NON comparable au leaderboard)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **2.5** | 27.5% | 25.0% | 47.5% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 5 | 37 | 6 |
| movie | 16 | 14 | 15 |
| music | 10 | 9 | 6 |
| open | 9 | 14 | 12 |
| sports | 15 | 21 | 11 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 7 | 7 | 6 |
| comparison | 5 | 15 | 1 |
| false_premise | 0 | 13 | 4 |
| multi-hop | 3 | 5 | 4 |
| post-processing | 2 | 3 | 4 |
| set | 5 | 10 | 8 |
| simple | 18 | 23 | 12 |
| simple_w_condition | 15 | 19 | 11 |

