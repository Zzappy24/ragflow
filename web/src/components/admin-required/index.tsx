// --- CYLLENE CUSTOM CODE ---
// Route guard for pages restricted to ws_admin, org_admin, and superuser.
// Viewers and Editors who navigate directly to a restricted URL see this message
// instead of the page content. The sidebar already hides the nav links, so
// this is a defense-in-depth measure for direct URL access.
// See CLAUDE.md "Custom B2B SaaS Multi-Tenant Layer" for merge warnings.

import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

export function AdminRequired({ children }: Props) {
  const { data: userInfo } = useFetchUserInfo();

  const isAdmin =
    !!userInfo?.is_superuser ||
    userInfo?.org_role === 'org_admin' ||
    userInfo?.ws_role === 'ws_admin';

  // Still loading — render nothing to avoid flash
  if (!userInfo) return null;

  if (!isAdmin) {
    return (
      <div className="flex items-center justify-center h-64 w-full border-[0.5px] border-border-button rounded-lg">
        <div className="text-center text-text-secondary max-w-sm">
          <p className="text-base font-medium mb-2">
            Access restricted to administrators.
          </p>
          <p className="text-sm">
            Contact your workspace admin to configure data sources and MCP
            servers.
          </p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
// --- END CYLLENE CUSTOM CODE ---
