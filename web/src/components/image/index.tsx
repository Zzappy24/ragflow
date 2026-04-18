import { Authorization } from '@/constants/authorization';
import { getAuthorization } from '@/utils/authorization-util';
import classNames from 'classnames';
import React, { forwardRef, useEffect, useState } from 'react';
import SvgIcon from '../svg-icon';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';

function useAuthBlobUrl(url: string) {
  const [blobUrl, setBlobUrl] = useState<string>();

  useEffect(() => {
    if (!url) return;
    let objectUrl: string | undefined;
    let cancelled = false;
    const activeWorkspaceId = localStorage.getItem('active_workspace_id');
    const headers: Record<string, string> = {
      [Authorization]: getAuthorization(),
    };
    if (activeWorkspaceId) {
      headers['X-Workspace-Id'] = activeWorkspaceId;
    }

    fetch(url, { headers })
      .then((res) => (res.ok ? res.blob() : null))
      .then((blob) => {
        if (blob && !cancelled) {
          objectUrl = URL.createObjectURL(blob);
          setBlobUrl(objectUrl);
        }
      })
      .catch(() => {});

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);

  return blobUrl;
}

export function useImageBlobUrl(id: string, t?: string | number) {
  return useAuthBlobUrl(`/v1/document/image/${id}${t ? `?_t=${t}` : ''}`);
}

export function useThumbnailBlobUrl(thumbnailUrl: string) {
  return useAuthBlobUrl(thumbnailUrl);
}

interface IImage extends React.ImgHTMLAttributes<HTMLImageElement> {
  id: string;
  t?: string | number;
  label?: string;
}

const Image = forwardRef<HTMLImageElement, IImage>(
  ({ id, t, label, className, ...props }, ref) => {
    const blobUrl = useImageBlobUrl(id, t);

    const imageElement = (
      <img
        {...props}
        ref={ref}
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
  },
);

Image.displayName = 'Image';

export default Image;

export function AuthThumbnail({
  url,
  extension,
  className,
}: {
  url?: string;
  extension?: string;
  className?: string;
}) {
  const blobUrl = useThumbnailBlobUrl(url ?? '');
  if (blobUrl) return <img src={blobUrl} alt="" className={className} />;
  return <SvgIcon name={`file-icon/${extension}`} width={24} />;
}

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
