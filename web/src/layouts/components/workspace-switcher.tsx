/**
 * WorkspaceSwitcher — topbar dropdown that lets a user switch the active
 * workspace context. Selection is persisted in localStorage as
 * ``active_workspace_id`` and all React Query caches are flushed so every
 * subsequent request picks up the new value via the request interceptor
 * (X-Workspace-Id header).
 *
 * The list comes from /v1/user/info, which already enriches the response with
 * the user's workspace memberships (and, for org_admins, every workspace in
 * their org). Backend RBAC remains the source of truth — this component only
 * mirrors what the user can already access.
 */
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { Routes } from '@/routes';
import { useQueryClient } from '@tanstack/react-query';
import { LucideBuilding2, LucideCheck, LucideChevronDown } from 'lucide-react';
import { useCallback, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router';

const ACTIVE_WORKSPACE_KEY = 'active_workspace_id';

export function WorkspaceSwitcher() {
  const { data: userInfo } = useFetchUserInfo();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const workspaces = userInfo?.workspaces ?? [];
  const storedId =
    typeof window !== 'undefined'
      ? localStorage.getItem(ACTIVE_WORKSPACE_KEY)
      : null;
  const activeId = userInfo?.active_workspace_id ?? storedId;

  // Auto-select the first workspace on first login (no stored preference yet).
  // This ensures X-Workspace-Id is sent on all subsequent requests so that
  // workspace-scoped endpoints (chats, datasets, etc.) don't return 401.
  useEffect(() => {
    if (!storedId && workspaces.length > 0) {
      localStorage.setItem(ACTIVE_WORKSPACE_KEY, workspaces[0].id);
      queryClient.clear();
    }
  }, [storedId, workspaces, queryClient]);

  const activeWs = useMemo(
    () => workspaces.find((w) => w.id === activeId) ?? workspaces[0],
    [workspaces, activeId],
  );

  const switchWorkspace = useCallback(
    (id: string) => {
      if (id === activeId) return;
      localStorage.setItem(ACTIVE_WORKSPACE_KEY, id);
      // Flush every cached query — they're all scoped to the old workspace.
      queryClient.clear();
      // Stay in the same section but escape detail pages whose IDs belong
      // to the previous workspace. E.g. /search/abc → /searches,
      // /chat/abc → /chats, /agent/abc → /agents. List pages and other
      // routes are kept as-is.
      const sectionListMap: Record<string, string> = {
        [Routes.Search]: Routes.Searches,
        [Routes.Chat]: Routes.Chats,
        [Routes.Agent]: Routes.Agents,
        [Routes.Memory]: Routes.Memories,
        [Routes.DatasetBase]: Routes.Datasets,
      };
      const path = window.location.pathname;
      let target = path;
      for (const [prefix, listRoute] of Object.entries(sectionListMap)) {
        // Match /search/:id, /chat/:id, etc. but not the list pages themselves
        if (path.startsWith(prefix + '/')) {
          target = listRoute;
          break;
        }
      }
      navigate(target, { replace: true });
    },
    [activeId, queryClient, navigate],
  );

  // Nothing to switch — hide the control entirely.
  if (workspaces.length === 0) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="flex items-center gap-2 max-w-[220px]"
          data-testid="workspace-switcher"
        >
          <LucideBuilding2 className="size-4 shrink-0" />
          <span className="truncate text-sm">
            {activeWs?.name ?? 'Select workspace'}
          </span>
          <LucideChevronDown className="size-[1em] shrink-0" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[240px]">
        <DropdownMenuLabel>Workspaces</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {workspaces.map((w) => (
          <DropdownMenuItem
            key={w.id}
            onClick={() => switchWorkspace(w.id)}
            className={w.id === activeId ? 'font-semibold' : ''}
          >
            <div className="flex flex-1 flex-col">
              <span className="truncate">{w.name}</span>
              <span className="text-xs text-text-secondary">{w.role}</span>
            </div>
            {w.id === activeId && (
              <LucideCheck className="size-4 shrink-0 text-text-secondary" />
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
