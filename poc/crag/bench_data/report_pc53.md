# CRAG — rapport de scoring

- Mode : corpus entier (NON comparable au leaderboard)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **-1.9** | 22.6% | 24.5% | 52.8% | 53 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 0 | 9 | 3 |
| movie | 6 | 7 | 2 |
| music | 1 | 1 | 1 |
| open | 1 | 2 | 5 |
| sports | 4 | 9 | 2 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 1 | 2 | 0 |
| comparison | 1 | 4 | 0 |
| false_premise | 0 | 2 | 1 |
| multi-hop | 0 | 2 | 2 |
| post-processing | 0 | 3 | 1 |
| set | 2 | 3 | 2 |
| simple | 6 | 5 | 4 |
| simple_w_condition | 2 | 7 | 3 |

