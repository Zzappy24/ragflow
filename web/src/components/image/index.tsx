import { Authorization } from '@/constants/authorization';
import { getAuthorization } from '@/utils/authorization-util';
import classNames from 'classnames';
import React, { forwardRef, useEffect, useState } from 'react';
import SvgIcon from '../svg-icon';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';

// CUSTOM B2B SaaS — cache mémoire des blob URLs. Le fetch authentifié
// (obligatoire pour X-Workspace-Id) prive les vignettes du cache HTTP du
// navigateur : sans cache applicatif, chaque montage re-téléchargeait
// chaque image → flash « vide puis rempli » à chaque changement de
// dataset. Les URLs cachées vivent pour la session (plafond FIFO, les
// évincées sont révoquées) ; `inflight` dédoublonne les fetchs concurrents
// d'une même image (liste de N docs au même thumbnail par ex.).
const blobUrlCache = new Map<string, string>();
const inflightFetches = new Map<string, Promise<string | null>>();
const BLOB_CACHE_MAX = 300;

function blobCacheKey(url: string) {
  // Le workspace actif change la réponse (auth) → il fait partie de la clé.
  return `${localStorage.getItem('active_workspace_id') ?? ''}|${url}`;
}

function fetchAuthBlobUrl(url: string, key: string): Promise<string | null> {
  const existing = inflightFetches.get(key);
  if (existing) return existing;

  const activeWorkspaceId = localStorage.getItem('active_workspace_id');
  const headers: Record<string, string> = {
    [Authorization]: getAuthorization(),
  };
  if (activeWorkspaceId) {
    headers['X-Workspace-Id'] = activeWorkspaceId;
  }

  const promise = fetch(url, { headers })
    .then((res) => (res.ok ? res.blob() : null))
    .then((blob) => {
      inflightFetches.delete(key);
      if (!blob) return null;
      const objectUrl = URL.createObjectURL(blob);
      if (blobUrlCache.size >= BLOB_CACHE_MAX) {
        const oldestKey = blobUrlCache.keys().next().value;
        if (oldestKey !== undefined) {
          URL.revokeObjectURL(blobUrlCache.get(oldestKey)!);
          blobUrlCache.delete(oldestKey);
        }
      }
      blobUrlCache.set(key, objectUrl);
      return objectUrl;
    })
    .catch(() => {
      inflightFetches.delete(key);
      return null;
    });

  inflightFetches.set(key, promise);
  return promise;
}

function useAuthBlobUrl(url: string) {
  const [blobUrl, setBlobUrl] = useState<string | undefined>(() =>
    url ? blobUrlCache.get(blobCacheKey(url)) : undefined,
  );

  useEffect(() => {
    if (!url) return;
    const key = blobCacheKey(url);
    const cached = blobUrlCache.get(key);
    if (cached) {
      setBlobUrl(cached);
      return;
    }
    let cancelled = false;
    fetchAuthBlobUrl(url, key).then((objectUrl) => {
      if (objectUrl && !cancelled) {
        setBlobUrl(objectUrl);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [url]);

  return blobUrl;
}

export function useImageBlobUrl(id: string, t?: string | number) {
  return useAuthBlobUrl(`/api/v1/documents/images/${id}${t ? `?_t=${t}` : ''}`);
}

// CUSTOM B2B SaaS — alias compatible avec l'export upstream `useDocumentImageUrl`
// introduit dans #16152. Upstream construit l'URL et fait un fetch direct ;
// notre version (`useImageBlobUrl`) fait un fetch authentifié et renvoie un
// blob URL pour respecter le contrat X-Workspace-Id du middleware api. Le
// résultat (string URL utilisable dans <img src>) est identique côté
// consommateurs (`reference-image-list.tsx` et autres).
export const useDocumentImageUrl = useImageBlobUrl;

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
