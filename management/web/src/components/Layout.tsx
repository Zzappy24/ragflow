import { useEffect } from 'react';
import { Outlet, useNavigate, useLocation, Link } from 'react-router-dom';
import { Layout as AntLayout, Menu, Button, Typography, Dropdown } from 'antd';
import {
  DashboardOutlined,
  BankOutlined,
  LogoutOutlined,
  UserOutlined,
  InboxOutlined,
  AuditOutlined,
  CodeOutlined,
} from '@ant-design/icons';
import cylleneLogo from '@/assets/cyllene-logo.svg';
import { useAuthStore } from '@/stores/auth';

const { Sider, Content, Header } = AntLayout;
const { Text } = Typography;

export default function AppLayout() {
  const { user, fetchMe, logout, isLoggedIn } = useAuthStore();

  // ws_admin-only users (no org_admin role anywhere) see a slimmed-down
  // sidebar. The org-scoped pages would 403 for them anyway — hiding the
  // entry prevents confusion and dead-end navigation.
  const hasOrgAdminElsewhere =
    !!user?.is_superuser ||
    !!user?.orgs?.some((o) => o.role === 'org_admin');

  const menuItems = [
    { key: '/', icon: <DashboardOutlined />, label: <Link to="/">Dashboard</Link> },
    ...(hasOrgAdminElsewhere
      ? [
          {
            key: '/organisations',
            icon: <BankOutlined />,
            label: <Link to="/organisations">Organisations</Link>,
          },
          {
            key: '/code',
            icon: <CodeOutlined />,
            label: <Link to="/code">Code</Link>,
          },
        ]
      : []),
    ...(user?.is_superuser
      ? [
          {
            key: '/archives',
            icon: <InboxOutlined />,
            label: <Link to="/archives">Archives</Link>,
          },
          {
            key: '/audit',
            icon: <AuditOutlined />,
            label: <Link to="/audit">Audit Global</Link>,
          },
        ]
      : []),
  ];
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    if (!isLoggedIn()) {
      navigate('/login');
      return;
    }
    if (!user) {
      fetchMe();
    }
  }, []);

  const userMenu = {
    items: [
      {
        key: 'logout',
        icon: <LogoutOutlined />,
        label: 'Sign out',
        onClick: logout,
      },
    ],
  };

  return (
    <AntLayout className="min-h-screen">
      {/* breakpoint lg : sous 992px le menu se replie en icônes (petit
          laptop/tablette) au lieu d'écraser le contenu. */}
      <Sider width={200} theme="light" className="border-r"
             breakpoint="lg" collapsible collapsedWidth={64}>
        <div className="p-4 text-center border-b">
          <img src={cylleneLogo} alt="Cyllene" className="h-5 mx-auto mb-1" />
          <Text strong className="text-xs text-gray-500 tracking-widest uppercase">Admin</Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[
            location.pathname.startsWith('/organisations') || location.pathname.startsWith('/workspaces')
              ? '/organisations'
              : location.pathname.startsWith('/code')
              ? '/code'
              : location.pathname.startsWith('/archives')
              ? '/archives'
              : location.pathname.startsWith('/audit')
              ? '/audit'
              : location.pathname,
          ]}
          items={menuItems}
          className="border-r-0 mt-2"
        />
      </Sider>
      <AntLayout>
        <Header className="bg-white border-b px-6 flex items-center justify-end h-14">
          <Dropdown menu={userMenu} placement="bottomRight">
            <Button type="text" icon={<UserOutlined />}>
              {user?.email || '...'}
            </Button>
          </Dropdown>
        </Header>
        <Content className="p-6 bg-gray-50">
          <Outlet />
        </Content>
      </AntLayout>
    </AntLayout>
  );
}
