import { IconFontFill } from '@/components/icon-font';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import ThemeSwitch from '@/components/theme-switch';
import { Button } from '@/components/ui/button';
import { Domain } from '@/constants/common';
import { useLogout } from '@/hooks/use-login-request';
import {
  useFetchSystemVersion,
  useFetchUserInfo,
} from '@/hooks/use-user-setting-request';
import { cn } from '@/lib/utils';
import { Routes } from '@/routes';
import { TFunction } from 'i18next';
import {
  LucideKeyRound,
  LucideLogOut,
  LucideServer,
  LucideUnplug,
  LucideUser,
} from 'lucide-react';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useHandleMenuClick } from './hooks';

const menuItems = (t: TFunction, isAdmin: boolean) => {
  const items: Array<{
    icon: JSX.Element;
    label: string;
    key: string;
    'data-testid'?: string;
  }> = [
    {
      icon: <LucideUser className="size-[1em]" />,
      label: t('setting.profile'),
      key: Routes.Profile,
    },
    // CUSTOM B2B SaaS — per-user API keys (anyone can mint a key scoped to
    // their own RBAC). Workspace-wide / org-wide key management lives in the
    // admin panel and is org_admin/ws_admin only.
    {
      icon: <LucideKeyRound className="size-[1em]" />,
      label: t('setting.apiKeys'),
      key: Routes.ApiKeys,
    },
  ];
  // CUSTOM B2B SaaS — Model configuration is managed exclusively via the admin
  // panel; the native Model route is intentionally excluded for all roles.
  // Same for Team and the chat-channels feature (#15850) — surfaced via
  // admin panel when we wire it up. See CLAUDE.md "Custom B2B SaaS Multi-Tenant
  // Layer" for merge warnings.
  if (isAdmin) {
    items.push(
      {
        icon: <LucideServer className="size-[1em]" />,
        label: t('setting.dataSources'),
        key: Routes.DataSource,
      },
      {
        icon: <IconFontFill name="mcp" className="size-[1em]" />,
        label: 'MCP',
        key: Routes.Mcp,
      },
      {
        icon: <LucideUnplug className="size-[1em]" />,
        label: t('setting.api'),
        key: Routes.Api,
      },
    );
  }
  return items;
};

export function SideBar() {
  const { data: userInfo } = useFetchUserInfo();
  const { handleMenuClick, active: activeItemKey } = useHandleMenuClick();
  const { version, fetchSystemVersion } = useFetchSystemVersion();
  const { t } = useTranslation();
  const isAdmin =
    !!userInfo?.is_superuser ||
    userInfo?.org_role === 'org_admin' ||
    userInfo?.ws_role === 'ws_admin';
  useEffect(() => {
    if (location.host !== Domain) {
      fetchSystemVersion();
    }
  }, [fetchSystemVersion]);
  const { logout } = useLogout();

  return (
    <aside className="shrink-0 w-16 md:w-[303px] bg-bg-base flex flex-col overflow-hidden">
      <header>
        <h1 className="px-2 md:px-6 flex gap-2.5 items-center justify-center md:justify-start font-normal">
          <RAGFlowAvatar
            avatar={userInfo?.avatar}
            name={userInfo?.nickname}
            isPerson
          />

          <p className="hidden md:block text-sm text-text-primary truncate">
            {userInfo?.email}
          </p>
        </h1>
      </header>

      <nav className="flex-1 overflow-auto mt-4 py-1">
        <ul className="px-2 md:px-6 flex flex-col gap-2 md:gap-5 items-center md:items-stretch">
          {menuItems(t, isAdmin).map((item) => {
            const { key, icon, label, ...rest } = item;

            return (
              <li key={key} className="w-full md:w-auto">
                <Button
                  {...rest}
                  block
                  variant="ghost"
                  aria-label={label}
                  className={cn(
                    'relative h-10 text-base max-md:size-10 max-md:p-0 max-md:justify-center justify-start gap-2.5 px-2 md:px-3',
                    activeItemKey === key && 'bg-bg-card text-text-primary',
                  )}
                  onClick={handleMenuClick(key)}
                >
                  <span className="flex items-center gap-2.5 max-md:gap-0">
                    {icon}
                    <span className="hidden md:inline">{label}</span>
                  </span>
                </Button>
              </li>
            );
          })}
        </ul>
      </nav>

      <footer className="p-2 md:p-6 mt-auto">
        <div className="hidden md:flex items-center gap-2 mb-6 justify-between">
          <span className="text-xs text-accent-primary">{version}</span>

          <ThemeSwitch />
        </div>

        <Button
          block
          size="lg"
          variant="transparent"
          aria-label={t('setting.logout')}
          className="max-md:size-10 max-md:p-0 max-md:mx-auto max-md:justify-center"
          onClick={() => logout()}
        >
          <LucideLogOut className="size-[1em] md:hidden" />
          <span className="hidden md:inline">{t('setting.logout')}</span>
        </Button>
      </footer>
    </aside>
  );
}
