import { Button } from '@/components/ui/button';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { Routes } from '@/routes';
import { useQueryClient } from '@tanstack/react-query';
import { LucideBuilding2, LucideCheck, LucideChevronDown } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';

// Deterministic color from string — same workspace always gets same color
function stringToColor(str: string): string {
  const palette = [
    '#6366f1',
    '#8b5cf6',
    '#ec4899',
    '#f59e0b',
    '#10b981',
    '#06b6d4',
    '#f97316',
    '#ef4444',
    '#84cc16',
    '#14b8a6',
  ];
  let hash = 0;
  for (let i = 0; i < str.length; i++)
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  return palette[Math.abs(hash) % palette.length];
}

function WorkspaceAvatar({ name, size = 22 }: { name: string; size?: number }) {
  const initials = name
    .split(/[\s\-_]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join('');
  const bg = stringToColor(name);
  return (
    <span
      style={{
        width: size,
        height: size,
        minWidth: size,
        background: bg,
        borderRadius: 5,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: size * 0.42,
        fontWeight: 700,
        color: '#fff',
        letterSpacing: '-0.02em',
        userSelect: 'none',
      }}
    >
      {initials || '?'}
    </span>
  );
}

const ACTIVE_WORKSPACE_KEY = 'active_workspace_id';

export function WorkspaceSwitcher() {
  const { data: userInfo } = useFetchUserInfo();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const workspaces = userInfo?.workspaces ?? [];
  const storedId =
    typeof window !== 'undefined'
      ? localStorage.getItem(ACTIVE_WORKSPACE_KEY)
      : null;
  const activeId = userInfo?.active_workspace_id ?? storedId;

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

  // Group by org
  const byOrg = useMemo(() => {
    const map = new Map<
      string,
      { org_id: string; org_name: string; items: typeof workspaces }
    >();
    for (const w of workspaces) {
      const orgName = w.org_name ?? w.org_id ?? 'Sans organisation';
      if (!map.has(w.org_id)) {
        map.set(w.org_id, { org_id: w.org_id, org_name: orgName, items: [] });
      }
      map.get(w.org_id)!.items.push(w);
    }
    return Array.from(map.values());
  }, [workspaces]);

  const switchWorkspace = useCallback(
    (id: string) => {
      if (id === activeId) return;
      localStorage.setItem(ACTIVE_WORKSPACE_KEY, id);
      queryClient.clear();
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
        if (path.startsWith(prefix + '/')) {
          target = listRoute;
          break;
        }
      }
      navigate(target, { replace: true });
      setOpen(false);
    },
    [activeId, queryClient, navigate],
  );

  if (workspaces.length === 0) return null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          role="combobox"
          aria-expanded={open}
          className="flex items-center gap-2 max-w-[260px]"
          data-testid="workspace-switcher"
        >
          {activeWs ? (
            <WorkspaceAvatar name={activeWs.name} size={20} />
          ) : (
            <LucideBuilding2 className="size-4 shrink-0" />
          )}
          <div className="flex flex-col items-start min-w-0">
            <span className="truncate text-sm leading-tight">
              {activeWs?.name ?? 'Workspace'}
            </span>
            {activeWs?.org_name && (
              <span className="truncate text-[10px] leading-tight text-muted-foreground">
                {activeWs.org_name}
              </span>
            )}
          </div>
          <LucideChevronDown className="size-[1em] shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[280px] p-0" align="end">
        <Command>
          <CommandInput placeholder="Rechercher un workspace..." autoFocus />
          <CommandList className="max-h-[320px]">
            <CommandEmpty>Aucun workspace trouvé.</CommandEmpty>
            {byOrg.map((group, gi) => (
              <span key={group.org_id}>
                {gi > 0 && <CommandSeparator />}
                <CommandGroup heading={group.org_name}>
                  {group.items.map((w) => (
                    <CommandItem
                      key={w.id}
                      value={`${group.org_name} ${w.name}`}
                      onSelect={() => switchWorkspace(w.id)}
                      className="flex items-center gap-2"
                    >
                      <WorkspaceAvatar name={w.name} size={24} />
                      <div className="flex flex-1 flex-col min-w-0">
                        <span className="truncate text-sm">{w.name}</span>
                        <span className="text-xs text-muted-foreground">
                          {w.role}
                        </span>
                      </div>
                      {w.id === activeId && (
                        <LucideCheck className="size-4 shrink-0 text-muted-foreground" />
                      )}
                    </CommandItem>
                  ))}
                </CommandGroup>
              </span>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
