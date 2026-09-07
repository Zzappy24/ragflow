// CUSTOM B2B SaaS — garde de workspace (2026-09-07).
// Avant ce composant, les pages connectées se rendaient AVANT que le sélecteur
// ait épinglé un workspace : la première rafale de requêtes partait sans
// X-Workspace-Id, le backend retombait sur le tenant personnel (aucun rôle) et
// répondait 403 — toasts, et l'accueil plantait sur `data: false`. Ici, rien
// n'est rendu tant qu'un workspace valide n'est pas épinglé ; le sélecteur
// garde son rôle de CHANGEMENT de workspace (et de re-validation d'un id
// périmé). Sans aucun workspace (tenant personnel), on laisse passer.
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

const ACTIVE_WORKSPACE_KEY = 'active_workspace_id';
const GATE_TIMEOUT_MS = 8000;

function readPinned(): string | null {
  try {
    return localStorage.getItem(ACTIVE_WORKSPACE_KEY);
  } catch {
    return null;
  }
}

export function WorkspaceGate({ children }: React.PropsWithChildren) {
  const { data: userInfo } = useFetchUserInfo();
  const queryClient = useQueryClient();
  const [ready, setReady] = useState<boolean>(() => !!readPinned());
  const workspaces = userInfo?.workspaces as Array<{ id: string }> | undefined;
  const preferredId = userInfo?.active_workspace_id as string | undefined;

  useEffect(() => {
    if (ready || !Array.isArray(workspaces)) {
      return;
    }
    if (workspaces.length === 0) {
      setReady(true); // aucun workspace : la page gère elle-même ce cas
      return;
    }
    const stored = readPinned();
    if (!workspaces.some((w) => w.id === stored)) {
      const fallback =
        workspaces.find((w) => w.id === preferredId)?.id ?? workspaces[0].id;
      try {
        localStorage.setItem(ACTIVE_WORKSPACE_KEY, fallback);
      } catch {
        // stockage indisponible : on rend quand même, le sélecteur retentera
      }
      queryClient.clear();
    }
    setReady(true);
  }, [ready, workspaces, preferredId, queryClient]);

  // Filet : /users/me injoignable ou très lent → ne pas bloquer l'écran.
  useEffect(() => {
    if (ready) {
      return;
    }
    const t = setTimeout(() => setReady(true), GATE_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [ready]);

  if (!ready) {
    return (
      <div
        className="size-full flex items-center justify-center text-sm text-text-secondary"
        data-testid="workspace-gate"
      />
    );
  }
  return <>{children}</>;
}
