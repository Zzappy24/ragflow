import { useSetModalState, useTranslate } from '@/hooks/common-hooks';
import { useFetchEmbedBeta } from '@/hooks/use-user-setting-request';
import { useCallback } from 'react';
import message from '../ui/message';

export const useShowTokenEmptyError = () => {
  const { t } = useTranslate('chat');

  const showTokenEmptyError = useCallback(() => {
    message.error(t('tokenError'));
  }, [t]);
  return { showTokenEmptyError };
};

export const useShowBetaEmptyError = () => {
  const { t } = useTranslate('chat');

  const showBetaEmptyError = useCallback(() => {
    message.error(t('betaError'));
  }, [t]);
  return { showBetaEmptyError };
};

// CUSTOM B2B SaaS — Intégrer / Partager : le bouton demande le seul jeton
// beta du bot (route ouverte aux éditeurs) au lieu de lister les clés API,
// réservées aux admins depuis l'audit 2026-09-06. Sans clé utilisable, le
// backend répond par un message explicite que l'intercepteur affiche.
export const useFetchTokenListBeforeOtherStep = (sharedId?: string) => {
  const { beta, fetchEmbedBeta } = useFetchEmbedBeta();

  const handleOperate = useCallback(async () => {
    const nextBeta = await fetchEmbedBeta(sharedId);
    return Boolean(nextBeta);
  }, [fetchEmbedBeta, sharedId]);

  return {
    token: '',
    beta,
    handleOperate,
  };
};

export const useShowEmbedModal = (sharedId?: string) => {
  const {
    visible: embedVisible,
    hideModal: hideEmbedModal,
    showModal: showEmbedModal,
  } = useSetModalState();

  const { handleOperate, token, beta } =
    useFetchTokenListBeforeOtherStep(sharedId);

  const handleShowEmbedModal = useCallback(async () => {
    const succeed = await handleOperate();
    if (succeed) {
      showEmbedModal();
    }
  }, [handleOperate, showEmbedModal]);

  return {
    showEmbedModal: handleShowEmbedModal,
    hideEmbedModal,
    embedVisible,
    embedToken: token,
    beta,
  };
};
