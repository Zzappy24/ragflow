/**
 * @deprecated This file will be deprecated. Please use `@web/src/utils/next-request.ts` instead.
 */

import message from '@/components/ui/message';
import { Authorization } from '@/constants/authorization';
import { ResponseType } from '@/interfaces/database/base';
import i18n from '@/locales/config';
import authorizationUtil, {
  getAuthorization,
  redirectToLogin,
} from '@/utils/authorization-util';
import notification from '@/utils/notification';
import { RequestMethod, extend } from 'umi-request';
import { convertTheKeysOfTheObjectToSnake, isFormData } from './common-util';
import { setCachedLlmList } from './llm-cache';
import { addTenantParams } from './llm-util';

const FAILED_TO_FETCH = 'Failed to fetch';

export const RetcodeMessage = {
  200: i18n.t('message.200'),
  201: i18n.t('message.201'),
  202: i18n.t('message.202'),
  204: i18n.t('message.204'),
  400: i18n.t('message.400'),
  401: i18n.t('message.401'),
  403: i18n.t('message.403'),
  404: i18n.t('message.404'),
  406: i18n.t('message.406'),
  410: i18n.t('message.410'),
  413: i18n.t('message.413'),
  422: i18n.t('message.422'),
  500: i18n.t('message.500'),
  502: i18n.t('message.502'),
  503: i18n.t('message.503'),
  504: i18n.t('message.504'),
};
export type ResultCode =
  | 200
  | 201
  | 202
  | 204
  | 400
  | 401
  | 403
  | 404
  | 406
  | 410
  | 413
  | 422
  | 500
  | 502
  | 503
  | 504;

const errorHandler = (error: {
  response: Response;
  message: string;
}): Response => {
  const { response } = error;
  if (error.message === FAILED_TO_FETCH) {
    notification.error({
      description: i18n.t('message.networkAnomalyDescription'),
      message: i18n.t('message.networkAnomaly'),
    });
  } else {
    if (response && response.status) {
      const errorText =
        RetcodeMessage[response.status as ResultCode] || response.statusText;
      const { status, url } = response;
      const requestId = response.headers?.get('X-Request-Id');
      notification.error({
        message: `${i18n.t('message.requestError')} ${status}: ${url}`,
        description: requestId ? `${errorText} (ref: ${requestId})` : errorText,
      });
    }
  }
  return response ?? { data: { code: 1999 } };
};

const request: RequestMethod = extend({
  errorHandler,
  timeout: 300000,
  getResponse: true,
});

// avoid duplicate 401 redirects
let isRedirecting = false;

request.interceptors.request.use((url: string, options: any) => {
  const data = convertTheKeysOfTheObjectToSnake(options.data);
  const params = convertTheKeysOfTheObjectToSnake(options.params);

  // Add tenant parameters to data
  const dataWithTenantParams = isFormData(data)
    ? data
    : addTenantParams(data, url);

  // RBAC: inject active workspace ID for multi-tenant resolution
  const activeWorkspaceId = localStorage.getItem('active_workspace_id');

  return {
    url,
    options: {
      ...options,
      data: dataWithTenantParams,
      params,
      headers: {
        ...(options.skipToken
          ? undefined
          : { [Authorization]: getAuthorization() }),
        ...(activeWorkspaceId
          ? { 'X-Workspace-Id': activeWorkspaceId }
          : undefined),
        ...options.headers,
      },
      interceptors: true,
    },
  };
});

// CUSTOM B2B SaaS — un 401 « workspace manquant » N'EST PAS une perte de
// session : il survient quand une requête part avant que le front ait épinglé
// un workspace (login /login, navigation privée, premier chargement). Le
// traiter comme une déconnexion créait une boucle de login (incident
// 2026-09-05). On le distingue par son message backend et on laisse passer
// sans purger le token ni rediriger — le sélecteur de workspace épingle un
// défaut, les appels suivants portent l'en-tête X-Workspace-Id.
const isWorkspaceContext401 = (msg?: string): boolean =>
  typeof msg === 'string' && /workspace/i.test(msg);

request.interceptors.response.use(async (response: Response, options) => {
  if (response?.status === 413 || response?.status === 504) {
    message.error(RetcodeMessage[response?.status as ResultCode]);
  }

  // Handle HTTP 401
  if (response?.status === 401) {
    if (!isRedirecting) {
      isRedirecting = true;

      const data = await response
        .clone()
        .json()
        .catch(() => ({}));

      const messageText = data?.message || RetcodeMessage[401];
      if (isWorkspaceContext401(data?.message)) {
        // Pas de déconnexion : contexte workspace non encore résolu.
        isRedirecting = false;
        return response;
      }
      notification.error({
        message: messageText,
        description: messageText,
        duration: 3,
      });
      authorizationUtil.removeAll();
      redirectToLogin();
    }

    return response;
  }

  if (options.responseType === 'blob') {
    return response;
  }

  const data: ResponseType = await response?.clone()?.json();

  // Update LLM list cache when fetching my_llm or llm_list
  if (data?.code === 0 && data?.data) {
    const url = response?.url || '';
    if (url.includes('/v1/llm/my_llms') || url.includes('/v1/llm/list')) {
      setCachedLlmList(data.data);
    }
  }

  if (data?.code === 100) {
    message.error(data?.message);
  } else if (data?.code === 401) {
    if (isWorkspaceContext401(data?.message)) {
      // Workspace non résolu : ne pas déconnecter (cf. isWorkspaceContext401).
      return response;
    }
    if (!isRedirecting) {
      isRedirecting = true;
      notification.error({
        message: data?.message,
        description: data?.message,
        duration: 3,
      });
      authorizationUtil.removeAll();
      redirectToLogin();
    }
    authorizationUtil.removeAll();
    redirectToLogin();
  } else if (data?.code !== 0) {
    notification.error({
      message: `${i18n.t('message.hint')} : ${data?.code}`,
      description: data?.message,
      duration: 3,
    });
  }
  return response;
});

export default request;

export const get = (url: string) => {
  return request.get(url);
};

export const post = (url: string, body: any) => {
  return request.post(url, { data: body });
};

export const drop = () => {};

export const put = () => {};
