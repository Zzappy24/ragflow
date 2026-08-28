# CRAG — rapport de scoring

- Mode : corpus entier (NON comparable au leaderboard)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **9.0** | 31.5% | 22.5% | 46.0% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 4 | 32 | 12 |
| movie | 18 | 18 | 9 |
| music | 9 | 10 | 6 |
| open | 13 | 13 | 9 |
| sports | 19 | 19 | 9 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 8 | 9 | 3 |
| comparison | 4 | 15 | 2 |
| false_premise | 1 | 12 | 4 |
| multi-hop | 4 | 6 | 2 |
| post-processing | 2 | 7 | 0 |
| set | 6 | 7 | 10 |
| simple | 18 | 21 | 14 |
| simple_w_condition | 20 | 15 | 10 |

