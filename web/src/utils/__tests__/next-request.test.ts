/**
 * CUSTOM B2B SaaS — pin de l'intercepteur axios (client /api/v1/*).
 *
 * Scénario de l'incident : appareil sans workspace épinglé, la page
 * d'accueil appelle GET /api/v1/searches → HTTP 401 « Unauthorized »
 * alors que le token est parfaitement valide. L'ancien intercepteur purgeait
 * le token et renvoyait au login → boucle. Il doit désormais rejeter la
 * requête (erreur de query) mais GARDER la session ; et ne déconnecter que
 * si la sonde /users/me confirme que le token est mort.
 */
jest.mock('@/utils/notification', () => ({
  __esModule: true,
  default: { error: jest.fn(), success: jest.fn() },
}));
jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { error: jest.fn(), success: jest.fn() },
}));
jest.mock('@/locales/config', () => ({
  __esModule: true,
  default: { t: (key: string) => key },
}));
jest.mock('@/utils/llm-cache', () => ({ setCachedLlmList: jest.fn() }));
jest.mock('@/utils/llm-util', () => ({ addTenantParams: (d: any) => d }));
jest.mock('@/utils/authorization-util', () => ({
  __esModule: true,
  default: { removeAll: jest.fn() },
  getAuthorization: jest.fn(() => 'signed-token'),
  redirectToLogin: jest.fn(),
}));

import authorizationUtil, { redirectToLogin } from '@/utils/authorization-util';
import request from '@/utils/next-request';
import { resetSessionGuardForTests } from '@/utils/session-guard';
import axios from 'axios';

const fetchMock = jest.fn();

function adapterReplying(status: number, body: unknown) {
  // `any` : esbuild-jest passe le source par babel sans preset TypeScript
  // (hoisting de jest.mock) et refuse un type importé en annotation.
  return async (config: any) => {
    const response = {
      status,
      statusText: status === 401 ? 'UNAUTHORIZED' : 'OK',
      headers: {},
      config,
      data: body,
    };
    if (status >= 400) {
      throw new axios.AxiosError(
        `Request failed with status code ${status}`,
        'ERR_BAD_REQUEST',
        config as any,
        null,
        response as any,
      );
    }
    return response;
  };
}

beforeEach(() => {
  (global as any).fetch = fetchMock;
  fetchMock.mockReset();
  jest.clearAllMocks();
  resetSessionGuardForTests();
});

describe('next-request — 401 HTTP (branche erreur axios)', () => {
  it('session vivante : la requête est rejetée mais le token est conservé', async () => {
    request.defaults.adapter = adapterReplying(401, {
      code: 401,
      message: 'Unauthorized',
    });
    fetchMock.mockResolvedValue({ status: 200 });

    await expect(request.get('/api/v1/searches')).rejects.toBeDefined();

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/users/me',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'signed-token' }),
      }),
    );
    expect(authorizationUtil.removeAll).not.toHaveBeenCalled();
    expect(redirectToLogin).not.toHaveBeenCalled();
  });

  it('session morte : purge du token et retour au login', async () => {
    request.defaults.adapter = adapterReplying(401, {
      code: 401,
      message: 'Unauthorized',
    });
    fetchMock.mockResolvedValue({ status: 401 });

    await expect(request.get('/api/v1/searches')).rejects.toBeDefined();

    expect(authorizationUtil.removeAll).toHaveBeenCalledTimes(1);
    expect(redirectToLogin).toHaveBeenCalledTimes(1);
  });

  it('401 « workspace » : ni sonde ni déconnexion', async () => {
    request.defaults.adapter = adapterReplying(401, {
      code: 401,
      message: 'No active workspace. Send X-Workspace-Id header.',
    });

    await expect(request.get('/api/v1/searches')).rejects.toBeDefined();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(redirectToLogin).not.toHaveBeenCalled();
  });
});

describe('next-request — code 401 dans un corps HTTP 200 (branche succès)', () => {
  it('session vivante : réponse rendue telle quelle, token conservé', async () => {
    request.defaults.adapter = adapterReplying(200, {
      code: 401,
      message: 'Unauthorized',
    });
    fetchMock.mockResolvedValue({ status: 200 });

    const res = await request.get('/api/v1/searches');

    expect(res.data.code).toBe(401);
    expect(authorizationUtil.removeAll).not.toHaveBeenCalled();
    expect(redirectToLogin).not.toHaveBeenCalled();
  });

  it('session morte : déconnexion', async () => {
    request.defaults.adapter = adapterReplying(200, {
      code: 401,
      message: 'Unauthorized',
    });
    fetchMock.mockResolvedValue({ status: 401 });

    await request.get('/api/v1/searches');

    expect(authorizationUtil.removeAll).toHaveBeenCalledTimes(1);
    expect(redirectToLogin).toHaveBeenCalledTimes(1);
  });
});
