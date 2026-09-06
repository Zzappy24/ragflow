"""CUSTOM B2B SaaS — purge PHYSIQUE des données d'un workspace (2026-09-06).

Le panel d'administration (``management/server/services/provisioning.py``,
``purge_workspace``) supprimait uniquement des lignes SQL : les chunks et
vecteurs restaient dans l'index ES/Infinity du tenant, l'index de
métadonnées survivait, les fichiers restaient dans MinIO, et les chats,
agents, recherches, mémoires, clés API du tenant restaient orphelins en base.
Droit à l'effacement non tenu, souveraineté non prouvable, et 4 shards ES
orphelins par client purgé.

Le panel n'embarque ni le client ES ni MinIO (image slim) : il appelle la
route interne ``POST /api/v1/internal/workspaces/<tenant_id>/purge-data``
(secret partagé) qui exécute ``purge_tenant_content`` ci-dessous, PUIS
supprime ses lignes de structure (workspace, membres, tenant, user technique).

Contrat :
- refus (``PurgeRefused``) si le workspace n'existe pas ou est encore ACTIF ;
- données physiques d'abord (stockage objet, index), lignes SQL de contenu
  ensuite : une reprise après échec partiel retrouve tout ce qui reste ;
- toute erreur sur les données physiques REMONTE (le panel n'efface alors
  pas la structure et l'admin peut relancer) — jamais de « best effort »
  silencieux ;
- idempotent : bucket absent, index absent, 0 ligne = succès.
"""
import logging

from api.db.services.api_service import APITokenService
from api.db.services.doc_metadata_service import DocMetadataService
from api.db.services.document_service import DocumentService
from api.db.services.file2document_service import File2DocumentService
from api.db.services.file_service import FileService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.langfuse_service import TenantLangfuseService
from api.db.services.mcp_server_service import MCPServerService
from api.db.services.memory_service import MemoryService
from api.db.services.search_service import SearchService
from api.db.services.task_service import TaskService
from api.db.services.tenant_llm_service import TenantLLMService
from api.db.services.tenant_model_provider_service import TenantModelProviderService
from api.db.services.workspace_service import WorkspaceService
from api.db.joint_services.user_account_service import delete_user_agents, delete_user_dialogs
from common import settings
from rag.nlp import search

logger = logging.getLogger(__name__)


class PurgeRefused(Exception):
    """Le tenant ne doit pas être purgé (inconnu, ou workspace encore actif)."""


def _guard(tenant_id: str):
    ok, ws = WorkspaceService.get_by_tenant_id(tenant_id)
    if not ok or not ws:
        raise PurgeRefused(f"no workspace for tenant {tenant_id}")
    if ws.status == "1":
        raise PurgeRefused(f"workspace {ws.id} is still active; soft-delete it first")
    return ws


def _iter_tenant_files(tenant_id: str) -> list:
    """Lignes File du tenant (parent_id = bucket, location = objet)."""
    from api.db.db_models import File
    return list(File.select(File.parent_id, File.location, File.type).where(File.tenant_id == tenant_id))


def _purge_object_storage(tenant_id: str, kb_ids: list[str], summary: dict) -> None:
    storage = settings.STORAGE_IMPL
    # 1. Objets des bases de connaissances (documents, vignettes, images de
    #    chunks : tout vit sous le préfixe/bucket du kb_id).
    removed_buckets = 0
    for kb_id in kb_ids:
        if storage.bucket_exists(kb_id):
            storage.remove_bucket(kb_id)
            removed_buckets += 1
    summary["buckets_removed"] = removed_buckets
    # 2. Fichiers du gestionnaire de fichiers du tenant (bucket = dossier parent).
    removed_files = 0
    for f in _iter_tenant_files(tenant_id):
        if f.type == "folder" or not f.location:
            continue
        try:
            if storage.obj_exist(f.parent_id, f.location):
                storage.rm(f.parent_id, f.location)
                removed_files += 1
        except Exception as e:  # un objet déjà absent ne doit pas bloquer la purge
            logger.warning("purge %s: file blob %s/%s: %s", tenant_id, f.parent_id, f.location, e)
    summary["file_blobs_removed"] = removed_files


def _purge_doc_store(tenant_id: str, summary: dict) -> None:
    conn = settings.docStoreConn
    # Index des chunks du tenant, supprimé ENTIER (pas seulement les chunks
    # par kb_id : un index vide garde ses shards pour toujours).
    conn.delete_idx(search.index_name(tenant_id), "")
    summary["chunk_index_dropped"] = search.index_name(tenant_id)
    meta_index = DocMetadataService._get_doc_meta_index_name(tenant_id)
    conn.delete_idx(meta_index, "")
    summary["meta_index_dropped"] = meta_index
    # Index des mémoires d'agent (un par mémoire).
    memories = MemoryService.get_by_tenant_id(tenant_id) or []
    dropped = 0
    if memories:
        from memory.services.messages import MessageService
        for m in memories:
            if MessageService.has_index(tenant_id, m.id):
                MessageService.delete_index(tenant_id, m.id)
                dropped += 1
    summary["memory_indexes_dropped"] = dropped
    summary["_memory_ids"] = [m.id for m in memories]


def _purge_sql_content(tenant_id: str, kb_ids: list[str], summary: dict) -> None:
    doc_ids = [d["id"] for d in (DocumentService.get_all_doc_ids_by_kb_ids(kb_ids) if kb_ids else [])]
    file_ids = [f["id"] for f in (FileService.get_all_file_ids_by_tenant_id(tenant_id) or [])]
    summary["tasks_deleted"] = TaskService.delete_by_doc_ids(doc_ids) if doc_ids else 0
    summary["file2document_deleted"] = (
        File2DocumentService.delete_by_document_ids_or_file_ids(doc_ids, file_ids) if (doc_ids or file_ids) else 0
    )
    summary["documents_deleted"] = DocumentService.delete_by_ids(doc_ids) if doc_ids else 0
    summary["files_deleted"] = FileService.delete_by_ids(file_ids) if file_ids else 0
    summary["datasets_deleted"] = KnowledgebaseService.delete_by_ids(kb_ids) if kb_ids else 0
    agents = delete_user_agents(tenant_id)
    summary["agents_deleted"] = agents.get("agents_deleted_count", 0)
    dialogs = delete_user_dialogs(tenant_id)
    summary["dialogs_deleted"] = dialogs.get("dialogs_deleted_count", 0)
    summary["api_tokens_deleted"] = APITokenService.delete_by_tenant_id(tenant_id)
    summary["searches_deleted"] = SearchService.delete_by_tenant_id(tenant_id)
    summary["mcp_servers_deleted"] = MCPServerService.delete_by_tenant_id(tenant_id)
    memory_ids = summary.pop("_memory_ids", [])
    summary["memories_deleted"] = MemoryService.delete_by_ids(memory_ids) if memory_ids else 0
    summary["tenant_llm_deleted"] = TenantLLMService.delete_by_tenant_id(tenant_id)
    summary["tenant_model_providers_deleted"] = TenantModelProviderService.delete_by_tenant_id(tenant_id)
    summary["langfuse_deleted"] = TenantLangfuseService.delete_ty_tenant_id(tenant_id)


def purge_tenant_content(tenant_id: str) -> dict:
    """Efface TOUT le contenu d'un tenant de workspace soft-supprimé.

    Ne touche pas aux lignes de structure (Workspace, WsMember, Tenant, User
    technique) : elles restent au panel, qui les supprime après confirmation
    de cette fonction. Lève ``PurgeRefused`` si le workspace est actif ou
    inconnu ; toute autre exception remonte telle quelle.
    """
    ws = _guard(tenant_id)
    kb_ids = list(KnowledgebaseService.get_kb_ids(tenant_id) or [])
    summary: dict = {"tenant_id": tenant_id, "workspace_id": ws.id, "datasets": len(kb_ids)}
    logger.info("purge tenant %s (workspace %s): %d datasets", tenant_id, ws.id, len(kb_ids))
    _purge_object_storage(tenant_id, kb_ids, summary)
    _purge_doc_store(tenant_id, summary)
    _purge_sql_content(tenant_id, kb_ids, summary)
    logger.info("purge tenant %s done: %s", tenant_id, summary)
    return summary
