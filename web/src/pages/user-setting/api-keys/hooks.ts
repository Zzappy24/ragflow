import message from '@/components/ui/message';
import {
  ApiKeyScope,
  CreateApiKeyPayload,
  createMyApiKey,
  listMyApiKeys,
  revokeMyApiKey,
} from '@/services/api-key-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { t } from 'i18next';

const QUERY_KEY = ['my-api-keys'];

// Runtime shape: umi-request is configured with `getResponse: true`, so a
// call resolves to `{ data: { code, message, data: T }, response }`. The
// project's `RequestMethod` typing only models the inner body, so we cast at
// the boundary to keep call-site code readable without sprinkling `as any`.
type Wrapped<T> = { data: { code: number; message: string; data: T } };

export const useListMyApiKeys = () => {
  const { data, isFetching } = useQuery<ApiKeyScope[]>({
    queryKey: QUERY_KEY,
    queryFn: async () => {
      const res = (await listMyApiKeys()) as unknown as Wrapped<{
        keys: ApiKeyScope[];
      }>;
      return res?.data?.data?.keys ?? [];
    },
  });
  return { keys: data ?? [], isFetching };
};

export const useCreateMyApiKey = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CreateApiKeyPayload) => {
      const res = (await createMyApiKey(
        payload,
      )) as unknown as Wrapped<ApiKeyScope>;
      if (res?.data?.code !== 0) {
        throw new Error(res?.data?.message || 'Failed to create API key');
      }
      return res.data.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      message.success(t('message.created'));
    },
    onError: (e: Error) => {
      message.error(e.message);
    },
  });
};

export const useRevokeMyApiKey = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = (await revokeMyApiKey(id)) as unknown as Wrapped<{
        id: string;
        deleted: boolean;
      }>;
      if (res?.data?.code !== 0) {
        throw new Error(res?.data?.message || 'Failed to revoke API key');
      }
      return res.data.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      message.success(t('message.deleted'));
    },
    onError: (e: Error) => {
      message.error(e.message);
    },
  });
};
