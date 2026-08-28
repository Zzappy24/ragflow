# CRAG — rapport de scoring

- Mode : corpus entier (NON comparable au leaderboard)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **5.0** | 24.5% | 19.5% | 56.0% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 5 | 36 | 7 |
| movie | 13 | 24 | 8 |
| music | 8 | 12 | 5 |
| open | 10 | 15 | 10 |
| sports | 13 | 25 | 9 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 5 | 10 | 5 |
| comparison | 5 | 15 | 1 |
| false_premise | 1 | 12 | 4 |
| multi-hop | 3 | 7 | 2 |
| post-processing | 2 | 5 | 2 |
| set | 2 | 13 | 8 |
| simple | 16 | 26 | 11 |
| simple_w_condition | 15 | 24 | 6 |

