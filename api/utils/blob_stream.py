"""
CUSTOM B2B SaaS — réponses de téléchargement streamées (perf front).

Symptôme corrigé : les routes de download faisaient `STORAGE_IMPL.get(...)`
en SYNCHRONE sur l'event-loop Quart (api = 1 worker async par pod) puis
chargeaient le blob entier en RAM (BytesIO). Télécharger un gros fichier
gelait TOUTES les autres requêtes du pod (chat, datasets, navigation)
pendant des secondes, avec un pic RAM de la taille du fichier.

Ici : ouverture et lecture chunk par chunk offloadées en thread, réponse
streamée — l'event-loop respire entre chaque chunk, mémoire constante.
Backend sans get_stream (S3/OSS/Azure) : fallback lecture complète mais
offloadée en thread (plus de gel de loop, seul le pic RAM subsiste).
"""
from urllib.parse import quote

from quart import Response

from common import settings
from common.misc_utils import thread_pool_exec

_SENTINEL = object()


def _content_disposition(filename: str) -> str:
    # RFC 5987 pour les noms non-ASCII, avec fallback ASCII.
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


async def stream_blob_response(bucket: str, name: str, filename: str,
                               mimetype: str = "application/octet-stream"):
    """Réponse de téléchargement streamée, ou None si l'objet est introuvable.

    Toutes les opérations bloquantes (open + chaque read de chunk) passent
    par le thread pool — jamais sur l'event-loop.
    """
    impl = settings.STORAGE_IMPL
    headers = {
        "Content-Disposition": _content_disposition(filename),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }

    if hasattr(impl, "get_stream"):
        it = await thread_pool_exec(impl.get_stream, bucket, name)
        if it is None:
            return None
        # Content-Length quand le backend sait la taille : sans lui le
        # navigateur ne peut afficher ni progression ni temps restant.
        if hasattr(impl, "obj_size"):
            size = await thread_pool_exec(impl.obj_size, bucket, name)
            if size is not None:
                headers["Content-Length"] = str(size)

        async def agen():
            while True:
                chunk = await thread_pool_exec(next, it, _SENTINEL)
                if chunk is _SENTINEL:
                    break
                yield chunk

        return Response(agen(), mimetype=mimetype or "application/octet-stream", headers=headers)

    # Backend sans streaming : lecture complète mais hors event-loop.
    blob = await thread_pool_exec(impl.get, bucket, name)
    if blob is None:
        return None
    return Response(blob, mimetype=mimetype or "application/octet-stream", headers=headers)
