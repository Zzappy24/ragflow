import { useFetchTokenListBeforeOtherStep } from '@/components/embed-dialog/use-show-embed-dialog';
import { Button } from '@/components/ui/button';
import { SharedFrom } from '@/constants/chat';
import { cn } from '@/lib/utils';
import { Routes } from '@/routes';
import { Send } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useFetchSearchDetail } from '../next-searches/hooks';
import EmbedAppModal from './embed-app-modal';

function EmbedIcon() {
  const [openEmbed, setOpenEmbed] = useState(false);
  const { data: SearchData } = useFetchSearchDetail();
  const { beta, handleOperate } = useFetchTokenListBeforeOtherStep(
    SearchData?.id,
  );

  return (
    <>
      <Button
        variant={'outline'}
        onClick={() => {
          handleOperate().then((res) => {
            if (res) {
              setOpenEmbed(!openEmbed);
            }
          });
        }}
      >
        <Send />
      </Button>
      <EmbedAppModal
        open={openEmbed}
        setOpen={setOpenEmbed}
        url={Routes.SearchShare}
        token={SearchData?.id as string}
        from={SharedFrom.Search}
        beta={beta}
      />
    </>
  );
}

// CUSTOM B2B SaaS — white-label : la page Recherche affichait le wordmark
// « RAGFlow » en dégradé (fuite de marque, vue 2026-09-07 sur la vidéo de
// démo). On affiche le nom de l'application de recherche, dans la couleur
// d'accent (donc celle de l'organisation quand elle est brandée).
export function RAGFlowLogo({
  onClick,
  showEmbedIcon = true,
}: {
  onClick?: React.MouseEventHandler<HTMLHeadingElement>;
  showEmbedIcon?: boolean;
}) {
  const { data: searchData } = useFetchSearchDetail();
  const { t } = useTranslation();
  return (
    <div className="flex gap-4 items-center">
      <h1
        onClick={onClick}
        className={cn('text-4xl font-bold text-accent-primary')}
      >
        {searchData?.name || t('header.search')}
      </h1>
      {showEmbedIcon && <EmbedIcon></EmbedIcon>}
    </div>
  );
}
