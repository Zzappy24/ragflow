# CRAG — rapport de scoring

- Mode : A (retrieval scopé aux pages de la question)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **7.0** | 30.0% | 23.0% | 47.0% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 5 | 35 | 8 |
| movie | 18 | 17 | 10 |
| music | 9 | 9 | 7 |
| open | 10 | 14 | 11 |
| sports | 18 | 19 | 10 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 8 | 8 | 4 |
| comparison | 6 | 15 | 0 |
| false_premise | 1 | 13 | 3 |
| multi-hop | 3 | 5 | 4 |
| post-processing | 2 | 4 | 3 |
| set | 5 | 9 | 9 |
| simple | 20 | 22 | 11 |
| simple_w_condition | 15 | 18 | 12 |

