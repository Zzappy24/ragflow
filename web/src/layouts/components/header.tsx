import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useChangeLanguage } from '@/hooks/logic-hooks';
import { useApplyOrgBranding } from '@/hooks/use-org-branding';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { cn } from '@/lib/utils';
import { Routes } from '@/routes';
import { LucideChevronDown } from 'lucide-react';
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router';
import GlobalNavbar from './global-navbar';
import ThemeButton from './theme-button';
import { WorkspaceSwitcher } from './workspace-switcher';

import { supportedLanguages } from '@/locales/config';

// CUSTOM B2B SaaS — seules fr/en sont servies aux clients ; les autres
// locales existent (upstream) mais contiennent encore la marque RAGFlow,
// donc grisées « sur demande » jusqu'à nettoyage au cas par cas.
const ENABLED_LANGUAGE_CODES = ['en', 'fr'];

export function Header({
  className,
  ...props
}: React.HTMLAttributes<HTMLElement>) {
  const { pathname } = useLocation();
  const { t } = useTranslation();

  const changeLanguage = useChangeLanguage();

  const {
    data: { language = 'en', avatar, nickname },
  } = useFetchUserInfo();

  const currentLanguage = supportedLanguages.find((x) => x.code === language);

  // const langItems = LanguageList.map((x) => ({
  //   key: x,
  //   label: <span>{LanguageMap[x as keyof typeof LanguageMap]}</span>,
  // }));

  const branding = useApplyOrgBranding();

  return (
    <header
      key="app-navbar"
      className={cn(
        'w-full grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] grid-rows-1 items-center gap-4',
        className,
      )}
      {...props}
    >
      <div className="inline-flex items-center">
        <Link
          to={Routes.Root}
          aria-current={pathname === Routes.Root ? 'page' : undefined}
        >
          <img
            src={branding?.logo || '/logo.svg'}
            alt={branding?.org_name || 'Cyllene'}
            className={
              branding?.logo ? 'h-10 w-auto max-w-40 object-contain' : 'size-10'
            }
          />
        </Link>
      </div>

      <GlobalNavbar />

      <div
        className="flex items-center justify-end gap-3 min-w-0 text-text-badge"
        data-testid="auth-status"
      >
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button className="flex items-center gap-1" variant="ghost">
              {currentLanguage?.displayName}
              <LucideChevronDown className="size-[1em]" />
            </Button>
          </DropdownMenuTrigger>

          <DropdownMenuContent>
            {supportedLanguages.map((x) => {
              const enabled = ENABLED_LANGUAGE_CODES.includes(x.code);
              return (
                <DropdownMenuItem
                  key={x.code}
                  disabled={!enabled}
                  onClick={() => enabled && changeLanguage(x.code)}
                >
                  {x.displayName}
                  {!enabled && (
                    <span className="ml-auto pl-4 text-xs text-text-secondary">
                      {t('common.languageOnRequest')}
                    </span>
                  )}
                </DropdownMenuItem>
              );
            })}
          </DropdownMenuContent>
        </DropdownMenu>

        <ThemeButton />

        <WorkspaceSwitcher />

        <Link
          to={Routes.UserSetting}
          className="relative ms-3"
          data-testid="settings-entrypoint"
        >
          <RAGFlowAvatar
            name={nickname}
            avatar={avatar}
            isPerson
            className="size-8"
          />
          {/* Temporarily hidden */}
          {/* <Badge className="h-5 w-8 absolute font-normal p-0 justify-center -right-8 -top-2 text-bg-base bg-gradient-to-l from-[#42D7E7] to-[#478AF5]">
            Pro
          </Badge> */}
        </Link>
      </div>
    </header>
  );
}
