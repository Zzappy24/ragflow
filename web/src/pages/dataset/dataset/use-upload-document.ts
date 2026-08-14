import { UploadFormSchemaType } from '@/components/file-upload-dialog';
import message from '@/components/ui/message';
import { useSetModalState } from '@/hooks/common-hooks';
import {
  useRunDocument,
  useUploadDocument,
} from '@/hooks/use-document-request';
import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

export const useHandleUploadDocument = () => {
  const { t } = useTranslation();
  const {
    visible: documentUploadVisible,
    hideModal: hideDocumentUploadModal,
    showModal: showDocumentUploadModal,
  } = useSetModalState();
  const { uploadDocument, loading } = useUploadDocument();
  const { runDocumentByIds } = useRunDocument();

  const onDocumentUploadOk = useCallback(
    async ({
      fileList,
      parseOnCreation,
      tableColumnMode,
      tableColumnRoles,
    }: UploadFormSchemaType) => {
      if (fileList.length > 0) {
        // Build parser_config if column roles are configured
        let parserConfig: Record<string, any> | undefined;
        if (
          tableColumnMode === 'manual' &&
          tableColumnRoles &&
          Object.keys(tableColumnRoles).length > 0
        ) {
          parserConfig = {
            table_column_mode: 'manual',
            table_column_roles: tableColumnRoles,
          };
        }

        const ret = await uploadDocument(fileList as File[], parserConfig);

        const results = ret?.results ?? [];
        const failed = results.filter((r) => !r.ok);
        const succeededCount = results.length - failed.length;
        const failedFileNames = failed.map((r) => r.name).join(', ');

        // Échec total : aucun fichier n'est passé (soit tous les uploads ont
        // échoué, soit la mutation a échoué avant même d'en lancer un — ex.
        // dataset ID manquant) — on garde le dialog ouvert pour laisser
        // l'utilisateur corriger et réessayer.
        if (ret?.code !== 0 && succeededCount === 0) {
          message.error(
            t('fileManager.uploadAllFailed', {
              files: failedFileNames || ret?.message,
            }),
          );
          return ret?.code;
        }

        if (parseOnCreation && ret?.data?.length) {
          runDocumentByIds({
            documentIds: ret.data.map((x: any) => x.id),
            run: 1,
          });
        }

        // Succès partiel : au moins un fichier est passé, le dialog se ferme
        // et un toast liste les fichiers en échec.
        if (failed.length > 0) {
          message.warning(
            t('fileManager.uploadPartialFailed', {
              failedCount: failed.length,
              totalCount: results.length,
              files: failedFileNames,
            }),
          );
        }

        hideDocumentUploadModal();
        return 0;
      }
    },
    [uploadDocument, runDocumentByIds, hideDocumentUploadModal, t],
  );

  return {
    documentUploadLoading: loading,
    onDocumentUploadOk,
    documentUploadVisible,
    hideDocumentUploadModal,
    showDocumentUploadModal,
  };
};
