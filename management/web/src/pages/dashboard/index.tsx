import { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, Spin } from 'antd';
import {
  BankOutlined,
  AppstoreOutlined,
  TeamOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';
import api from '@/lib/api';
import { useAuthStore } from '@/stores/auth';

interface Stats {
  total_orgs: number;
  total_workspaces: number;
  total_users: number;
  total_datasets: number;
  total_documents: number;
}

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const user = useAuthStore((s) => s.user);

  useEffect(() => {
    if (user?.is_superuser) {
      api
        .get('/system/stats')
        .then((res) => setStats(res.data))
        .catch(() => {})
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, [user]);

  if (loading) return <Spin size="large" className="flex justify-center mt-20" />;

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Dashboard</h2>

      {stats && (
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title="Organisations"
                value={stats.total_orgs}
                prefix={<BankOutlined />}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title="Workspaces"
                value={stats.total_workspaces}
                prefix={<AppstoreOutlined />}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title="Users"
                value={stats.total_users}
                prefix={<TeamOutlined />}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title="Datasets"
                value={stats.total_datasets}
                prefix={<DatabaseOutlined />}
              />
            </Card>
          </Col>
        </Row>
      )}

      {!user?.is_superuser && (
        <Card>
          <p className="text-gray-500">
            Welcome, {user?.email}. Select an organisation or workspace from the sidebar.
          </p>
          {user?.orgs && user.orgs.length > 0 && (
            <div className="mt-4">
              <h3 className="font-medium mb-2">Your organisations:</h3>
              <ul>
                {user.orgs.map((org) => (
                  <li key={org.org_id}>
                    {org.org_name} — <span className="text-blue-500">{org.role}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
