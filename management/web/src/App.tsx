import { Routes, Route } from 'react-router-dom';
import AppLayout from '@/components/Layout';
import LoginPage from '@/pages/login';
import DashboardPage from '@/pages/dashboard';
import OrganisationsPage from '@/pages/organisations';
import OrgDetailPage from '@/pages/organisations/detail';
import WorkspacesPage from '@/pages/workspaces';
import WorkspaceDetailPage from '@/pages/workspaces/detail';
import MembersPage from '@/pages/members';
import GroupsPage from '@/pages/groups';
import ApiKeysPage from '@/pages/api-keys';
import AuditPage from '@/pages/audit';

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
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
        <Route path="/audit" element={<AuditPage />} />
      </Route>
    </Routes>
  );
}
