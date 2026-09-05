import api from '@/utils/api';
import registerServer from '@/utils/register-server';
import request from '@/utils/request';

const {
  listFile,
  removeFile,
  uploadFile,
  getAllParentFolder,
  createFolder,
  connectFileToKnowledge,
  getDocumentFile,
  getFile,
  moveFile,
  getDatasetDocumentFileDownload,
  getAttachmentFileDownload,
  getDocumentDownloadToken,
  getFileDownloadToken,
} = api;

const methods = {
  listFile: {
    url: listFile,
    method: 'get',
  },
  removeFile: {
    url: removeFile,
    method: 'delete',
  },
  uploadFile: {
    url: uploadFile,
    method: 'post',
  },
  getAllParentFolder: {
    url: getAllParentFolder,
    method: 'get',
  },
  createFolder: {
    url: createFolder,
    method: 'post',
  },
  connectFileToKnowledge: {
    url: connectFileToKnowledge,
    method: 'post',
  },
  getFile: {
    url: getFile,
    method: 'get',
    responseType: 'blob',
  },
  getDocumentFile: {
    url: getDocumentFile,
    method: 'get',
    responseType: 'blob',
  },
  moveFile: {
    url: moveFile,
    method: 'post',
  },
} as const;

const fileManagerService = registerServer<keyof typeof methods>(
  methods,
  request,
);

export const downloadAgentFile = (data: { docId: string; ext: string }) => {
  return request.get(getAttachmentFileDownload(data.docId), {
    params: { ext: data.ext },
    responseType: 'blob',
  });
};

export const downloadDatasetDocument = (data: {
  datasetId: string;
  docId: string;
  ext: string;
}) => {
  return request.get(
    getDatasetDocumentFileDownload(data.datasetId, data.docId),
    {
      params: { ext: data.ext },
      responseType: 'blob',
    },
  );
};
// CUSTOM B2B SaaS — téléchargement direct : jeton court émis par le backend
// puis lien natif → barre de téléchargement du navigateur (progression,
// annulation), zéro blob en RAM de l'onglet. Renvoie false si le backend ne
// connaît pas encore la route, pour laisser l'appelant se replier.
const openDirectDownload = async (
  tokenUrl: string,
  filename: string,
): Promise<boolean> => {
  const res = await request.post(tokenUrl);
  const url: string | undefined = res?.data?.data?.url;
  if (!url) return false;
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  return true;
};

export const downloadDocumentDirect = (docId: string, filename: string) =>
  openDirectDownload(getDocumentDownloadToken(docId), filename);

export const downloadFileDirect = (fileId: string, filename: string) =>
  openDirectDownload(getFileDownloadToken(fileId), filename);

export default fileManagerService;
