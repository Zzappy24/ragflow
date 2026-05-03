import api from '@/utils/api';
import request from '@/utils/request';

export interface ApiKeyScope {
  id: string;
  name: string;
  workspace_id: string;
  permissions: string[];
  created_by: string;
  create_time: number;
  update_time: number;
  expires_at: string | null;
  last_used_at: string | null;
  status: string;
  token_preview: string;
  /** Only returned by createApiKey — never re-displayed by listApiKeys. */
  token?: string;
}

export interface CreateApiKeyPayload {
  name: string;
  permissions: string[];
  expires_at?: string | null;
}

type ResponseData<T> = {
  code: number;
  message: string;
  data: T;
};

export const listMyApiKeys = () =>
  request.get<ResponseData<{ keys: ApiKeyScope[] }>>(api.listMyApiKeys);

export const createMyApiKey = (payload: CreateApiKeyPayload) =>
  request.post<ResponseData<ApiKeyScope>>(api.createMyApiKey, {
    data: payload,
  });

export const revokeMyApiKey = (id: string) =>
  request.delete<ResponseData<{ id: string; deleted: boolean }>>(
    api.revokeMyApiKey(id),
  );
