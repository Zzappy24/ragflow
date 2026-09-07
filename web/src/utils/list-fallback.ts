// CUSTOM B2B SaaS — réponses de liste robustes aux refus RBAC (2026-09-07).
// Sur un 403 « Permission denied », le backend répond `{code: 403, data: false}` :
// un repli `data?.data ?? fallback` laisse passer `false` et le composant
// plante (`false.chats.slice`). Vu en prod à chaque connexion depuis une
// navigation privée : l'accueil liste chats et bases AVANT que le workspace
// soit épinglé, le backend retombe sur le tenant personnel et refuse.
export function safeListData<T>(
  res: { code?: number; data?: unknown } | null | undefined,
  fallback: T,
  isValid: (value: unknown) => boolean = (value) =>
    value !== null && typeof value === 'object',
): T {
  if (!res || res.code !== 0) {
    return fallback;
  }
  return isValid(res.data) ? (res.data as T) : fallback;
}

/** Vrai tant que le sélecteur n'a pas épinglé de workspace : un 403 reçu
 *  dans cet état est prématuré (tenant personnel), pas une vraie interdiction. */
export function isWorkspacePinned(): boolean {
  try {
    return !!localStorage.getItem('active_workspace_id');
  } catch {
    return true;
  }
}
