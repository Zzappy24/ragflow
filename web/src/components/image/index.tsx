import { Authorization } from '@/constants/authorization';
import { api_host } from '@/utils/api';
import { getAuthorization } from '@/utils/authorization-util';
import classNames from 'classnames';
import React, { useEffect, useState } from 'react';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';

interface IImage extends React.ImgHTMLAttributes<HTMLImageElement> {
  id: string;
  t?: string | number;
  label?: string;
}

const Image = ({ id, t, label, className, ...props }: IImage) => {
  const [blobUrl, setBlobUrl] = useState<string>();

  useEffect(() => {
    let revoked = false;
    const url = `${api_host}/document/image/${id}${t ? `?_t=${t}` : ''}`;
    const activeWorkspaceId = localStorage.getItem('active_workspace_id');
    const headers: Record<string, string> = {
      [Authorization]: getAuthorization(),
    };
    if (activeWorkspaceId) {
      headers['X-Workspace-Id'] = activeWorkspaceId;
    }

    fetch(url, { headers })
      .then((res) => {
        if (res.ok) return res.blob();
        return null;
      })
      .then((blob) => {
        if (blob && !revoked) {
          setBlobUrl(URL.createObjectURL(blob));
        }
      })
      .catch(() => {});

    return () => {
      revoked = true;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [id, t]);

  const imageElement = (
    <img
      {...props}
      src={blobUrl}
      className={classNames('max-w-[45vw] max-h-[40wh] block', className)}
    />
  );

  if (!label) {
    return imageElement;
  }

  return (
    <div className="relative inline-block w-full">
      {imageElement}
      <div className="absolute bottom-2 right-2 bg-accent-primary text-white px-2 py-0.5 rounded-xl text-xs font-normal backdrop-blur-sm">
        {label}
      </div>
    </div>
  );
};

export default Image;

export const ImageWithPopover = ({ id }: { id: string }) => {
  return (
    <Popover>
      <PopoverTrigger>
        <Image id={id} className="max-h-[100px] inline-block"></Image>
      </PopoverTrigger>
      <PopoverContent>
        <Image id={id} className="max-w-[100px] object-contain"></Image>
      </PopoverContent>
    </Popover>
  );
};
