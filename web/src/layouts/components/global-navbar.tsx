import { useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router';

import {
  Brain,
  Cpu,
  FolderOpen,
  Library,
  LucideHouse,
  MessageSquareText,
  Search,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import { Routes } from '@/routes';
import { supportsCssAnchor } from '@/utils/css-support';

// Active pill: foreground color as background (dark in light mode, light in dark mode)
// Active text: background color as text (white in light mode, near-black in dark mode)
const activePillStyle = {
  backgroundColor: 'hsl(var(--foreground))',
  color: 'var(--bg-base)',
} as const;

const PathMap = {
  [Routes.Datasets]: [Routes.Datasets, Routes.DatasetBase],
  [Routes.Chats]: [Routes.Chats, Routes.Chat],
  [Routes.Searches]: [Routes.Searches, Routes.Search],
  [Routes.Agents]: [Routes.Agents, Routes.AgentTemplates],
  [Routes.Memories]: [Routes.Memories, Routes.Memory, Routes.MemoryMessage],
  [Routes.Files]: [Routes.Files],
} as const;

// Match on path-segment boundaries, not a loose substring, so e.g.
// "/user-setting/chat-channel" does not match the "/chat" tab.
const matchesPath = (pathname: string, candidate: string) =>
  pathname === candidate || pathname.startsWith(`${candidate}/`);

// Chaque entrée a une icône : en mode « replié » (libellés qui ne tiennent
// pas dans le header — zoom > 125 %, petit écran, langue longue) la nav
// n'affiche que les icônes, le libellé reste en title/aria-label.
const menuItems = [
  { path: Routes.Root, name: 'header.home', icon: LucideHouse, iconOnly: true },
  { path: Routes.Datasets, name: 'header.dataset', icon: Library },
  {
    path: Routes.Chats,
    name: 'header.chat',
    icon: MessageSquareText,
    'data-testid': 'nav-chat',
  },
  {
    path: Routes.Searches,
    name: 'header.search',
    icon: Search,
    'data-testid': 'nav-search',
  },
  {
    path: Routes.Agents,
    name: 'header.flow',
    icon: Cpu,
    'data-testid': 'nav-agent',
  },
  { path: Routes.Memories, name: 'header.memories', icon: Brain },
  { path: Routes.Files, name: 'header.fileManager', icon: FolderOpen },
];

// Replie les libellés en icônes quand la nav déborde de son conteneur
// (le div scrollable du header) et les rétablit dès que la place revient.
// Mesure : la largeur « pleine » (libellés visibles) est mémorisée tant
// qu'on n'est pas replié ; replié, on compare la place disponible à cette
// largeur mémorisée (hystérésis de 8 px pour éviter le clignotement).
function useCollapsedLabels(language: string) {
  const navRef = useRef<HTMLElement>(null);
  const fullWidthRef = useRef(0);
  const [collapsed, setCollapsed] = useState(false);

  useLayoutEffect(() => {
    // Un changement de langue change la largeur pleine : on rouvre pour
    // la remesurer.
    setCollapsed(false);
  }, [language]);

  useLayoutEffect(() => {
    const nav = navRef.current;
    const container = nav?.parentElement;
    if (!nav || !container) return;

    const measure = () => {
      const available = container.clientWidth;
      if (!collapsed) {
        fullWidthRef.current = nav.scrollWidth;
        if (nav.scrollWidth > available) setCollapsed(true);
      } else if (
        fullWidthRef.current &&
        available >= fullWidthRef.current + 8
      ) {
        setCollapsed(false);
      }
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(container);
    return () => ro.disconnect();
  }, [collapsed]);

  return { navRef, collapsed };
}

const GlobalNavbar = supportsCssAnchor
  ? () => {
      const { t, i18n } = useTranslation();
      const { pathname } = useLocation();
      const { navRef, collapsed } = useCollapsedLabels(i18n.language);
      const navbarAnchorNamePrefix = useId().replace(/:/g, '');
      const activePath = useMemo(() => {
        return (
          Object.keys(PathMap).find((x: string) =>
            PathMap[x as keyof typeof PathMap].some((y: string) =>
              matchesPath(pathname, y),
            ),
          ) || pathname
        );
      }, [pathname]);

      const activePathAnchorName = `--${navbarAnchorNamePrefix}${activePath === Routes.Root ? '-root' : activePath.replace('/', '-')}`;

      const hasAnyActive = useMemo(
        () => menuItems.some(({ path }) => path === activePath),
        [activePath],
      );

      return (
        <nav className="mx-auto" ref={navRef}>
          <ul className="relative flex items-center p-1 bg-bg-card rounded-full border border-border-button">
            {menuItems.map(({ path, name, icon: Icon, iconOnly, ...props }) => {
              const isActive = path === activePath;
              const anchorName = `--${navbarAnchorNamePrefix}${path === Routes.Root ? '-root' : path.replace('/', '-')}`;

              return (
                <li key={path} className="relative" style={{ anchorName }}>
                  <Link
                    {...props}
                    to={path}
                    className="h-10 px-2.5 xl:px-5 text-sm xl:text-base inline-flex items-center justify-center whitespace-nowrap hover:text-current focus-visible:text-current rounded-full transition-all"
                    style={isActive ? activePillStyle : undefined}
                    title={t(name)}
                    aria-label={t(name)}
                    aria-current={isActive ? 'page' : undefined}
                  >
                    {iconOnly || collapsed ? (
                      <Icon className="size-6 stroke-[1.5]" />
                    ) : (
                      <span>{t(name)}</span>
                    )}
                  </Link>
                </li>
              );
            })}

            <li
              className={cn(
                'absolute -z-[1] border-b-2 border-b-accent-primary rounded-full opacity-0',
                'transition-all',
                hasAnyActive && 'opacity-100',
              )}
              role="presentation"
              style={{
                top: 'anchor(top)',
                left: 'anchor(left)',
                width: 'anchor-size(width)',
                height: 'anchor-size(height)',
                positionAnchor: activePathAnchorName,
                backgroundColor: 'hsl(var(--foreground))',
              }}
            />
          </ul>
        </nav>
      );
    }
  : () => {
      const { t, i18n } = useTranslation();
      const { pathname } = useLocation();
      const { navRef, collapsed } = useCollapsedLabels(i18n.language);
      const activePath = useMemo(() => {
        return (
          Object.keys(PathMap).find((x: string) =>
            PathMap[x as keyof typeof PathMap].some((y: string) =>
              matchesPath(pathname, y),
            ),
          ) || pathname
        );
      }, [pathname]);

      return (
        <nav className="mx-auto" ref={navRef}>
          <ul className="flex items-center p-1 bg-bg-card rounded-full border border-border-button">
            {menuItems.map(({ path, name, icon: Icon, iconOnly, ...props }) => {
              const isActive = path === activePath;

              return (
                <li key={path}>
                  <Link
                    {...props}
                    to={path}
                    className={cn(
                      'h-10 px-2.5 xl:px-5 text-sm xl:text-base inline-flex items-center justify-center whitespace-nowrap',
                      'hover:text-current focus-visible:text-current rounded-full transition-all',
                      isActive &&
                        'border-b-2 border-b-accent-primary rounded-full',
                    )}
                    style={isActive ? activePillStyle : undefined}
                    title={t(name)}
                    aria-label={t(name)}
                    aria-current={isActive ? 'page' : undefined}
                  >
                    {iconOnly || collapsed ? (
                      <Icon className="size-6 stroke-[1.5]" />
                    ) : (
                      <span>{t(name)}</span>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      );
    };

export default GlobalNavbar;
