import bannerBg from '@/assets/banner-cyllene.jpg';
import { Card, CardContent } from '@/components/ui/card';
import { useFetchOrgBranding } from '@/hooks/use-org-branding';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { ArrowRight, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

function BannerCard() {
  return (
    <Card className="w-auto border-none h-3/4">
      <CardContent className="p-4">
        <span className="inline-block bg-backgroundCoreWeak rounded-sm px-1 text-xs">
          System
        </span>
        <div className="flex mt-1 gap-4">
          <span className="text-lg truncate">Setting up your LLM</span>
          <ArrowRight />
        </div>
      </CardContent>
    </Card>
  );
}

export function Banner() {
  return (
    <section className="bg-[url('@/assets/banner.png')] bg-cover h-28 rounded-2xl  my-8 flex gap-8 justify-between">
      <div className="h-full text-3xl font-bold items-center inline-flex ml-6">
        Welcome to Cyllene
      </div>
      <div className="flex justify-between items-center gap-4 mr-5">
        <BannerCard></BannerCard>
        <BannerCard></BannerCard>
        <BannerCard></BannerCard>
        <button
          type="button"
          className="relative p-1 hover:bg-white/10 rounded-full transition-colors"
        >
          <X className="w-6 h-6 text-white" />
        </button>
      </div>
    </section>
  );
}

// CUSTOM B2B SaaS — bannière d'accueil par organisation (2026-09-07).
// banner_mode 'org' : image de l'org si fournie, sinon bannière générée depuis
// sa couleur d'accent et son logo. Sinon (défaut) : la montagne Cyllene.
function OrgBannerBackground({
  branding,
}: {
  branding: ReturnType<typeof useFetchOrgBranding>;
}) {
  if (branding?.banner) {
    return (
      <>
        <img
          src={branding.banner}
          alt=""
          className="absolute inset-0 size-full object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-r from-black/65 via-black/35 to-black/10" />
      </>
    );
  }
  const accent = branding?.brand_color || 'rgb(var(--accent-primary))';
  return (
    <>
      <div
        className="absolute inset-0"
        style={{
          background: `linear-gradient(115deg, ${accent} 0%, color-mix(in srgb, ${accent} 55%, black) 100%)`,
        }}
      />
      {branding?.logo && (
        <img
          src={branding.logo}
          alt={branding.org_name || ''}
          className="absolute right-10 top-1/2 -translate-y-1/2 max-h-16 max-w-48 object-contain opacity-90"
        />
      )}
    </>
  );
}

export function NextBanner() {
  const { t, i18n } = useTranslation();
  const {
    data: { nickname },
  } = useFetchUserInfo();
  const branding = useFetchOrgBranding();
  const orgBanner = branding?.banner_mode === 'org';
  return (
    <section className="relative rounded-2xl overflow-hidden my-8">
      {orgBanner ? (
        <OrgBannerBackground branding={branding} />
      ) : (
        <>
          <img
            src={bannerBg}
            alt=""
            className="absolute inset-0 size-full object-cover"
          />
          <div className="absolute inset-0 bg-gradient-to-r from-black/65 via-black/35 to-black/10" />
        </>
      )}
      <h1
        className="relative px-10 py-12 text-5xl leading-normal text-left"
        dir={i18n.language?.startsWith('ar') ? 'rtl' : 'ltr'}
      >
        <span className="font-semibold text-white/90">
          {t('header.welcome')}{' '}
        </span>
        <span className="font-bold text-white border-b-4 border-accent-primary">
          {nickname}
        </span>
      </h1>
    </section>
  );
}
