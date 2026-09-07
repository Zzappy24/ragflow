// CUSTOM B2B SaaS — DA par organisation : applique la couleur d'accent et
// le logo de l'org du workspace actif. Sans branding (tenant personnel, org
// non personnalisée, requête en échec), le thème Cyllene reste intact.
import brandingService from '@/services/branding-service';
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';

export interface IOrgBranding {
  logo?: string | null;
  brand_color?: string | null;
  org_name?: string | null;
  /** 'cyllene' (défaut) ou 'org' : bannière d'accueil de l'organisation */
  banner_mode?: 'cyllene' | 'org' | null;
  /** image data-URI de la bannière ; absente = bannière générée (couleur + logo) */
  banner?: string | null;
}

const ACTIVE_WORKSPACE_KEY = 'active_workspace_id';

export function useFetchOrgBranding() {
  const workspaceId =
    typeof localStorage !== 'undefined'
      ? localStorage.getItem(ACTIVE_WORKSPACE_KEY)
      : null;

  const { data } = useQuery<IOrgBranding>({
    queryKey: ['orgBranding', workspaceId],
    staleTime: 5 * 60 * 1000,
    retry: false,
    queryFn: async () => {
      const { data: res } = await brandingService.getOrgBranding();
      return res?.code === 0 ? (res.data ?? {}) : {};
    },
  });

  return data;
}

/** Pose --accent-primary depuis le #rrggbb de l'org (inline style sur
 *  :root, prioritaire sur la feuille) et le retire au retour Cyllene. */
export function useApplyOrgBranding(): IOrgBranding | undefined {
  const branding = useFetchOrgBranding();

  useEffect(() => {
    const root = document.documentElement;
    const hex = branding?.brand_color;
    if (hex && /^#[0-9a-fA-F]{6}$/.test(hex)) {
      const r = parseInt(hex.slice(1, 3), 16);
      const g = parseInt(hex.slice(3, 5), 16);
      const b = parseInt(hex.slice(5, 7), 16);
      root.style.setProperty('--accent-primary', `${r} ${g} ${b}`);
    } else {
      root.style.removeProperty('--accent-primary');
    }
    return () => {
      root.style.removeProperty('--accent-primary');
    };
  }, [branding?.brand_color]);

  return branding;
}
