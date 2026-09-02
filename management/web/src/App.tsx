import { Routes, Route, Navigate } from 'react-router-dom';
import AppLayout from '@/components/Layout';
import LoginPage from '@/pages/login';
import ClaimPage from '@/pages/claim';
import OrgInvitePage from '@/pages/org-invite';
import DashboardPage from '@/pages/dashboard';
import OrganisationsPage from '@/pages/organisations';
import OrgDetailPage from '@/pages/organisations/detail';
import WorkspacesPage from '@/pages/workspaces';
import WorkspaceDetailPage from '@/pages/workspaces/detail';
import MembersPage from '@/pages/members';
import GroupsPage from '@/pages/groups';
import ApiKeysPage from '@/pages/api-keys';
import AuditPage from '@/pages/audit';
import GlobalAuditPage from '@/pages/audit/global';
import ArchivesPage from '@/pages/archives';
import CodePage from '@/pages/code';
import { useAuthStore } from '@/stores/auth';

function SuperuserRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuthStore();
  if (user && !user.is_superuser) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/claim" element={<ClaimPage />} />
      <Route path="/org-invite" element={<OrgInvitePage />} />
      <Route element={<AppLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="/organisations" element={<OrganisationsPage />} />
        <Route path="/organisations/:orgId" element={<OrgDetailPage />} />
        <Route path="/organisations/:orgId/workspaces" element={<WorkspacesPage />} />
        <Route path="/organisations/:orgId/members" element={<MembersPage />} />
        <Route path="/organisations/:orgId/audit" element={<AuditPage />} />
        <Route path="/workspaces" element={<WorkspacesPage />} />
        <Route path="/workspaces/:wsId" element={<WorkspaceDetailPage />} />
        <Route path="/workspaces/:wsId/members" element={<MembersPage />} />
        <Route path="/workspaces/:wsId/groups" element={<GroupsPage />} />
        <Route path="/workspaces/:wsId/api-keys" element={<ApiKeysPage />} />
        <Route path="/workspaces/:wsId/audit" element={<AuditPage />} />
        <Route path="/members" element={<MembersPage />} />
        <Route path="/groups" element={<GroupsPage />} />
        <Route path="/api-keys" element={<ApiKeysPage />} />
        <Route path="/audit" element={<SuperuserRoute><GlobalAuditPage /></SuperuserRoute>} />
        <Route path="/archives" element={<SuperuserRoute><ArchivesPage /></SuperuserRoute>} />
        <Route path="/code" element={<CodePage />} />
        {/* URL inconnue sous /admin -> dashboard plutôt que page blanche */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
