# CRAG — rapport de scoring

- Mode : A (retrieval scopé aux pages de la question)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **5.5** | 29.0% | 23.5% | 47.5% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 5 | 33 | 10 |
| movie | 17 | 18 | 10 |
| music | 9 | 9 | 7 |
| open | 11 | 14 | 10 |
| sports | 16 | 21 | 10 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 7 | 10 | 3 |
| comparison | 5 | 14 | 2 |
| false_premise | 0 | 15 | 2 |
| multi-hop | 3 | 5 | 4 |
| post-processing | 2 | 2 | 5 |
| set | 5 | 8 | 10 |
| simple | 18 | 23 | 12 |
| simple_w_condition | 18 | 18 | 9 |

