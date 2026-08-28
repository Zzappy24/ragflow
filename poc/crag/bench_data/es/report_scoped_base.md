# CRAG — rapport de scoring

- Mode : A (retrieval scopé aux pages de la question)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **8.5** | 27.0% | 18.5% | 54.5% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 5 | 36 | 7 |
| movie | 14 | 24 | 7 |
| music | 9 | 12 | 4 |
| open | 11 | 14 | 10 |
| sports | 15 | 23 | 9 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 7 | 8 | 5 |
| comparison | 4 | 16 | 1 |
| false_premise | 1 | 12 | 4 |
| multi-hop | 4 | 6 | 2 |
| post-processing | 2 | 4 | 3 |
| set | 2 | 12 | 9 |
| simple | 16 | 28 | 9 |
| simple_w_condition | 18 | 23 | 4 |

