/**
 * CUSTOM B2B SaaS — garde de session partagée par les deux clients HTTP
 * (`request.ts`, legacy /v1 ; `next-request.ts`, axios /api/v1).
 *
 * Un 401 n'est PAS forcément une perte de session. Sur un appareil sans
 * `active_workspace_id` (nouveau téléphone, navigation privée, stockage
 * purgé par Safari après 7 jours sans visite, id périmé), les requêtes
 * scopées workspace partent avant que le sélecteur ait épinglé un défaut ;
 * le backend répond alors 401 avec le message GÉNÉRIQUE « Unauthorized »
 * (`server_error_response` réécrit la description « No active workspace »).
 * Traiter ce 401 comme une déconnexion purgeait le token et renvoyait au
 * login → boucle « login → accueil → clignote → login » (incidents
 * 2026-09-05 et 2026-09-06, « suivant les appareils »). Le message n'est
 * donc PAS un critère fiable : on sonde la session elle-même
 * (GET /api/v1/users/me, route qui ne requiert pas de workspace) et on ne
 * déconnecte que si ELLE répond 401.
 */
import { Authorization } from '@/constants/authorization';
import api from '@/utils/api';
import authorizationUtil, {
  getAuthorization,
  redirectToLogin,
} from '@/utils/authorization-util';
import notification from '@/utils/notification';

/** Raccourci sans réseau quand le backend a gardé un message explicite. */
export const isWorkspaceContext401 = (msg?: string): boolean =>
  typeof msg === 'string' && /workspace/i.test(msg);

let probe: Promise<boolean> | null = null;
let logoutStarted = false;

/** Sonde single-flight : une rafale de 401 simultanés → une seule requête. */
export function isSessionAlive(): Promise<boolean> {
  const auth = getAuthorization();
  if (!auth) {
    return Promise.resolve(false);
  }
  if (!probe) {
    probe = fetch(api.userInfo, {
      headers: { [Authorization]: auth },
      cache: 'no-store',
    })
      .then((r) => r.status !== 401)
      // Réseau KO : on ne déconnecte pas sur un doute — la requête d'origine
      // a échoué de toute façon, l'utilisateur réessaiera.
      .catch(() => true)
      .finally(() => {
        probe = null;
      });
  }
  return probe;
}

/**
 * À appeler par les intercepteurs sur tout 401 (statut HTTP ou code
 * applicatif). Déconnecte (purge du token + retour au login) UNE seule fois,
 * et seulement si la session est réellement morte.
 */
export async function handleUnauthorized(messageText?: string): Promise<void> {
  if (logoutStarted) {
    return;
  }
  if (isWorkspaceContext401(messageText)) {
    return; // contexte workspace non résolu, pas un problème d'auth
  }
  if (await isSessionAlive()) {
    return;
  }
  if (logoutStarted) {
    return; // une sonde concurrente a déjà tranché
  }
  logoutStarted = true;
  const text = messageText || 'Unauthorized';
  notification.error({ message: text, description: text, duration: 3 });
  authorizationUtil.removeAll();
  redirectToLogin();
}

export function resetSessionGuardForTests(): void {
  probe = null;
  logoutStarted = false;
}
