import { getExtension } from '@/utils/document-util';

import { AuthThumbnail } from '@/components/image';
import { useFetchDocumentThumbnailsByIds } from '@/hooks/use-document-request';
import { useEffect } from 'react';
import styles from './index.module.less';

interface IProps {
  name: string;
  id: string;
}

const FileIcon = ({ name, id }: IProps) => {
  const fileExtension = getExtension(name);

  const { data: fileThumbnails, setDocumentIds } =
    useFetchDocumentThumbnailsByIds();
  const fileThumbnail = fileThumbnails[id];

  useEffect(() => {
    if (id) {
      setDocumentIds([id]);
    }
  }, [id, setDocumentIds]);

  return (
    <AuthThumbnail
      url={fileThumbnail}
      extension={fileExtension}
      className={styles.thumbnailImg}
    />
  );
};

export default FileIcon;
