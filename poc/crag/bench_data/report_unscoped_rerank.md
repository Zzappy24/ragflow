# CRAG — rapport de scoring

- Mode : corpus entier (NON comparable au leaderboard)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **9.5** | 31.5% | 22.0% | 46.5% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 6 | 33 | 9 |
| movie | 16 | 17 | 12 |
| music | 9 | 10 | 6 |
| open | 12 | 15 | 8 |
| sports | 20 | 18 | 9 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 8 | 10 | 2 |
| comparison | 5 | 14 | 2 |
| false_premise | 1 | 13 | 3 |
| multi-hop | 3 | 6 | 3 |
| post-processing | 2 | 2 | 5 |
| set | 5 | 7 | 11 |
| simple | 20 | 24 | 9 |
| simple_w_condition | 19 | 17 | 9 |

