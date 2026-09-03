import api from '@/utils/api';
import { registerNextServer } from '@/utils/register-server';

const { getOrgBranding } = api;

const methods = {
  getOrgBranding: {
    url: getOrgBranding,
    method: 'get',
  },
} as const;

const brandingService = registerNextServer<keyof typeof methods>(methods);

export default brandingService;
