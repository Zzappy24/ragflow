# CRAG — rapport de scoring

- Mode : A (retrieval scopé aux pages de la question)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **8.0** | 29.0% | 21.0% | 50.0% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 4 | 33 | 11 |
| movie | 16 | 19 | 10 |
| music | 9 | 11 | 5 |
| open | 12 | 14 | 9 |
| sports | 17 | 23 | 7 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 7 | 9 | 4 |
| comparison | 5 | 15 | 1 |
| false_premise | 0 | 13 | 4 |
| multi-hop | 3 | 6 | 3 |
| post-processing | 2 | 5 | 2 |
| set | 6 | 10 | 7 |
| simple | 16 | 23 | 14 |
| simple_w_condition | 19 | 19 | 7 |

