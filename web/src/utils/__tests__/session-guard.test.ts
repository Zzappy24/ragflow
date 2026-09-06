/**
 * CUSTOM B2B SaaS — pin de la boucle de login (incidents 2026-09-05/06).
 *
 * Un 401 reçu par un client HTTP n'est PAS forcément une perte de session :
 * un appareil sans `active_workspace_id` (nouveau téléphone, navigation
 * privée, stockage purgé par Safari après 7 jours) lance les requêtes de la
 * page d'accueil avant que le sélecteur de workspace ait épinglé un défaut ;
 * le backend répond 401 avec le message GÉNÉRIQUE « Unauthorized »
 * (server_error_response réécrit la description) → impossible de le
 * distinguer d'un token expiré par son message. La seule preuve fiable est
 * de sonder la session elle-même : GET /api/v1/users/me (sans workspace)
 * répond 200 si le token est bon, 401 s'il est mort.
 */
jest.mock('@/utils/notification', () => ({
  __esModule: true,
  default: { error: jest.fn(), success: jest.fn() },
}));
jest.mock('@/utils/authorization-util', () => ({
  __esModule: true,
  default: { removeAll: jest.fn() },
  getAuthorization: jest.fn(() => 'signed-token'),
  redirectToLogin: jest.fn(),
}));

import authorizationUtil, {
  getAuthorization,
  redirectToLogin,
} from '@/utils/authorization-util';
import notification from '@/utils/notification';
import {
  handleUnauthorized,
  isWorkspaceContext401,
  resetSessionGuardForTests,
} from '@/utils/session-guard';

const fetchMock = jest.fn();

beforeEach(() => {
  (global as any).fetch = fetchMock;
  fetchMock.mockReset();
  jest.clearAllMocks();
  (getAuthorization as jest.Mock).mockReturnValue('signed-token');
  resetSessionGuardForTests();
});

describe('isWorkspaceContext401', () => {
  it('reconnaît les deux messages backend « workspace »', () => {
    expect(
      isWorkspaceContext401('No active workspace. Send X-Workspace-Id header.'),
    ).toBe(true);
    expect(isWorkspaceContext401('X-Workspace-Id header is required')).toBe(
      true,
    );
    expect(isWorkspaceContext401('Unauthorized')).toBe(false);
    expect(isWorkspaceContext401(undefined)).toBe(false);
  });
});

describe('handleUnauthorized', () => {
  it('401 « workspace » : jamais de déconnexion, et pas même de sonde', async () => {
    await handleUnauthorized(
      'No active workspace. Send X-Workspace-Id header.',
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(authorizationUtil.removeAll).not.toHaveBeenCalled();
    expect(redirectToLogin).not.toHaveBeenCalled();
  });

  it('401 générique avec session vivante (/users/me → 200) : on garde le token', async () => {
    fetchMock.mockResolvedValue({ status: 200 });
    await handleUnauthorized('Unauthorized');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/users/me');
    expect(init.headers.Authorization).toBe('signed-token');
    expect(init.headers['X-Workspace-Id']).toBeUndefined();
    expect(authorizationUtil.removeAll).not.toHaveBeenCalled();
    expect(redirectToLogin).not.toHaveBeenCalled();
    expect(notification.error).not.toHaveBeenCalled();
  });

  it('rafale de 401 sur session vivante : une seule sonde (single-flight)', async () => {
    fetchMock.mockResolvedValue({ status: 200 });
    await Promise.all([
      handleUnauthorized('Unauthorized'),
      handleUnauthorized('Unauthorized'),
      handleUnauthorized('Unauthorized'),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(redirectToLogin).not.toHaveBeenCalled();
  });

  it('session morte (/users/me → 401) : déconnexion UNE fois, même sous rafale', async () => {
    fetchMock.mockResolvedValue({ status: 401 });
    await Promise.all([
      handleUnauthorized('Unauthorized'),
      handleUnauthorized('Unauthorized'),
      handleUnauthorized('<Unauthorized 401>'),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(authorizationUtil.removeAll).toHaveBeenCalledTimes(1);
    expect(redirectToLogin).toHaveBeenCalledTimes(1);
    expect(notification.error).toHaveBeenCalledTimes(1);
  });

  it('aucun token en local : déconnexion directe, sans sonde', async () => {
    (getAuthorization as jest.Mock).mockReturnValue('');
    await handleUnauthorized('Unauthorized');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(redirectToLogin).toHaveBeenCalledTimes(1);
  });

  it('sonde en échec réseau : on ne déconnecte pas sur un doute', async () => {
    fetchMock.mockRejectedValue(new Error('Failed to fetch'));
    await handleUnauthorized('Unauthorized');
    expect(redirectToLogin).not.toHaveBeenCalled();
    expect(authorizationUtil.removeAll).not.toHaveBeenCalled();
  });

  it('après une sonde vivante, une sonde ultérieure repart (pas de cache définitif)', async () => {
    fetchMock.mockResolvedValueOnce({ status: 200 });
    await handleUnauthorized('Unauthorized');
    fetchMock.mockResolvedValueOnce({ status: 401 });
    await handleUnauthorized('Unauthorized');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(redirectToLogin).toHaveBeenCalledTimes(1);
  });
});
