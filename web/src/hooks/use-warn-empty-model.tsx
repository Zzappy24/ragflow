import { Modal } from '@/components/ui/modal/modal';
import { isEmpty } from 'lodash';
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

// CUSTOM B2B SaaS — model configuration is admin-panel-only. The upstream
// modal pointed users to Settings > Model providers (a page we removed from
// the sidebar); it now shows a "contact your administrator" message and no
// longer navigates anywhere. See CLAUDE.md "Custom files to watch".
export const useWarnEmptyModel = (
  showEmptyModelWarn: boolean,
  embdId?: string,
  llmId?: string,
  loading?: boolean,
) => {
  const { t } = useTranslation();
  const warnedRef = useRef(false);

  useEffect(() => {
    if (
      showEmptyModelWarn &&
      !warnedRef.current &&
      !loading &&
      (isEmpty(embdId) || isEmpty(llmId)) &&
      typeof embdId === 'string' &&
      typeof llmId === 'string'
    ) {
      warnedRef.current = true;
      Modal.warning({
        title: t('common.warn'),
        content: <div>{t('setting.modelProvidersWarnAdmin')}</div>,
        closable: false,
        showCancel: false,
      });
    }
  }, [showEmptyModelWarn, embdId, llmId, loading, t]);
};
