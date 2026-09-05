import { inspectorServer } from '@react-dev-inspector/vite-plugin';
import react from '@vitejs/plugin-react';
import https from 'https';
import path from 'path';
import { defineConfig, loadEnv } from 'vite';
import { createHtmlPlugin } from 'vite-plugin-html';
import { viteStaticCopy } from 'vite-plugin-static-copy';
import { appName } from './src/conf.json';

// Inject code location data attributes for react-dev-inspector
const inspectorBabelPlugin = (): import('vite').Plugin => ({
  name: 'inspector-babel',
  enforce: 'pre' as const,
  async transform(code: string, id: string) {
    if (id.includes('node_modules')) return;
    if (!/\.[jt]sx$/.test(id)) return;

    // Dynamically import babel transform to inject data attributes
    const { transform } = await import('@react-dev-inspector/babel-plugin');
    return {
      code: transform({
        filePath: id,
        sourceCode: code,
      }),
      map: null,
    };
  },
});

type MinifyValue = boolean | 'esbuild' | 'terser';

function resolveMinify(value: string | undefined): MinifyValue {
  if (value === undefined) return 'terser';
  const lower = value.toLowerCase();
  if (lower === 'false') return false;
  if (lower === 'esbuild') return 'esbuild';
  if (lower === 'terser') return 'terser';
  return 'terser';
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // Load env from .env file (also loads .env.local, .env.[mode], .env.[mode].local)
  const env = loadEnv(mode, process.cwd(), '');

  // Try to load from .env file explicitly if API_PROXY_SCHEME not found
  let proxyScheme = env.API_PROXY_SCHEME;
  if (!proxyScheme) {
    try {
      const envLocal = loadEnv('', process.cwd(), '');
      proxyScheme = envLocal.API_PROXY_SCHEME;
    } catch {
      // ignore
    }
  }
  proxyScheme = proxyScheme || 'python';

  console.log(`[vite.config] mode: ${mode}, API_PROXY_SCHEME: ${proxyScheme}`);

  const proxySchemes = {
    python: {
      '/api/v1/admin': {
        target: 'http://127.0.0.1:9381/',
        changeOrigin: true,
        ws: true,
      },
      '/api': {
        target: 'http://127.0.0.1:9380/',
        changeOrigin: true,
        ws: true,
      },
      '/v1': {
        target: 'http://127.0.0.1:9380/',
        changeOrigin: true,
        ws: true,
      },
    },
    hybrid: {
      '^(/v1/kb)|^(/v1/document)|^(/v1/llm/list)|^(/api/v1/datasets)|^(/api/v1/memories)|^(/v1/user)|^(/v1/user/tenant_info)|^(/v1/tenant/list)|^(/v1/system/config)|^(/v1/user/login)|^(/v1/user/logout)|^(/api/v1/files)':
        {
          target: 'http://127.0.0.1:9384/',
          changeOrigin: true,
          ws: true,
        },
      '^(/api/v1/admin/sandbox)|^(/api/v1/admin/roles)|^(/api/v1/admin/roles/owner/permission)|^(/api/v1/admin/roles_with_permission)|^(/api/v1/admin/whitelist)|^(/api/v1/admin/variables)':
        {
          target: 'http://127.0.0.1:9381/',
          changeOrigin: true,
          ws: true,
        },
      '/api/v1/admin': {
        target: 'http://127.0.0.1:9383/',
        changeOrigin: true,
        ws: true,
      },
      '/api/v1/users/me/models': {
        target: 'http://127.0.0.1:9380/',
        changeOrigin: true,
        ws: true,
      },
      '^(/api/v1/users)|^(/api/v1/auth)|^(/api/v1/system/config)|^(/api/v1/system/version)|^(/api/v1/tenants)|^(/api/v1/chats)|^(/api/v1/searches)|^(/api/v1/files)|^(/api/v1/agents)':
        {
          target: 'http://127.0.0.1:9384/',
          changeOrigin: true,
          ws: true,
        },
      '/api': {
        target: 'http://127.0.0.1:9380/',
        changeOrigin: true,
        ws: true,
      },
      '/v1': {
        target: 'http://127.0.0.1:9380/',
        changeOrigin: true,
        ws: true,
      },
    },
    go: {
      '/api/v1/admin': {
        target: 'http://127.0.0.1:9383/',
        changeOrigin: true,
        ws: true,
      },
      '/api': {
        target: 'http://127.0.0.1:9384/',
        changeOrigin: true,
        ws: true,
      },
      '/v1': {
        target: 'http://127.0.0.1:9384/',
        changeOrigin: true,
        ws: true,
      },
    },
  };

  let proxy = proxySchemes[proxyScheme] || proxySchemes.python;

  // CUSTOM B2B SaaS — proxy vers un backend distant (dev front sans stack local)
  // API_PROXY_TARGET=https://ragflow.cyllene.cloud dans web/.env.local (gitignoré)
  // route TOUT /api et /v1 vers ce hôte. L'auth est en en-têtes (Authorization,
  // X-Workspace-Id), pas en cookies, donc changeOrigin suffit. SSE et WS passent.
  const proxyTarget = env.API_PROXY_TARGET;
  if (proxyTarget) {
    console.log(`[vite.config] API_PROXY_TARGET: ${proxyTarget}`);
    // Agent keep-alive : le navigateur émet des dizaines de requêtes en rafale ;
    // sans lui chaque requête ouvre sa propre connexion TLS vers le bord distant
    // (coûteux, et fragile quand le bord perd des handshakes — vécu 2026-09-04).
    const agent = new https.Agent({
      keepAlive: true,
      keepAliveMsecs: 30_000,
      maxSockets: 6,
    });
    // Plafond sur la SEULE phase de négociation TLS (8 s) : un bord qui ne
    // répond pas au ClientHello bloquait la socket sans limite, le navigateur
    // épuisait ses 6 connexions vers Vite et toute la page gelait. On ne touche
    // pas aux requêtes établies (SSE/chat longs), seul le handshake est borné.
    const TLS_HANDSHAKE_TIMEOUT_MS = 8_000;
    const configure = (proxy: import('http-proxy').default) => {
      if (process.env.PROXY_DEBUG)
        console.error('[proxy-debug] configure() appelé');
      // http-proxy-3 (Vite 7) émet 'proxyReq' DEPUIS l'événement 'socket' de la
      // requête sortante et passe la socket en 5e argument — s'abonner à
      // proxyReq.on('socket') ici arriverait trop tard (événement déjà passé).
      proxy.on('proxyReq', (proxyReq, _req, _res, _opts, socket) => {
        if (!socket) return;
        const tlsSocket = socket as import('tls').TLSSocket;
        // Socket neuve : connecting=true (TCP en cours) et getProtocol() vaut la
        // chaîne 'unknown' tant que le handshake n'est pas fini — PAS null.
        // Socket keep-alive réutilisée : connecting=false et un vrai 'TLSv1.x'.
        const proto =
          typeof tlsSocket.getProtocol === 'function'
            ? tlsSocket.getProtocol()
            : null;
        const reused = !socket.connecting && !!proto && proto !== 'unknown';
        if (process.env.PROXY_DEBUG) {
          console.error(
            `[proxy-debug] socket ${reused ? 'RÉUTILISÉE' : 'NEUVE'} connecting=${socket.connecting} pour ${proxyReq.path}`,
          );
        }
        // Socket keep-alive réutilisée : handshake déjà fait, rien à borner.
        if (reused) return;
        const timer = setTimeout(() => {
          console.error(`[proxy-debug] handshake expiré pour ${proxyReq.path}`);
          proxyReq.destroy(
            new Error(
              `TLS handshake > ${TLS_HANDSHAKE_TIMEOUT_MS} ms vers ${proxyTarget}`,
            ),
          );
        }, TLS_HANDSHAKE_TIMEOUT_MS);
        const clear = () => clearTimeout(timer);
        socket.once('secureConnect', clear);
        socket.once('close', clear);
        socket.once('error', clear);
      });
    };
    const remote = {
      target: proxyTarget,
      changeOrigin: true,
      secure: true,
      ws: true,
      agent,
      configure,
    };
    proxy = { '/api': remote, '/v1': remote };
  }
  // CUSTOM B2B SaaS — fin proxy distant

  return {
    define: {
      // Expose to client code via import.meta.env
      'import.meta.env.API_PROXY_SCHEME': JSON.stringify(proxyScheme),
      // Keep backward compatibility
      __API_PROXY_SCHEME__: JSON.stringify(proxyScheme),
    },
    plugins: [
      inspectorBabelPlugin(),
      react(),
      viteStaticCopy({
        targets: [
          {
            src: 'src/conf.json',
            dest: './',
          },
          {
            src: 'node_modules/monaco-editor/min/vs/',
            dest: './',
          },
        ],
      }),
      createHtmlPlugin({
        inject: {
          data: {
            title: appName,
          },
        },
      }),
      inspectorServer(),
    ],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
        '@parent': path.resolve(__dirname, '../'),
      },
    },
    css: {
      modules: {
        localsConvention: 'camelCase',
      },
      postcss: './postcss.config.js',
      preprocessorOptions: {
        less: {
          javascriptEnabled: true,
          additionalData: `
            @import "@/less/variable.less";
            @import "@/less/mixins.less";
          `,
          modifyVars: {
            hack: `true; @import "@/less/index.less";`,
          },
        },
      },
    },
    server: {
      port: Number(env.PORT) || 9222,
      strictPort: false,
      hmr: {
        overlay: false,
      },
      proxy,
    },
    assetsInclude: ['**/*.md'],
    base: env.VITE_BASE_URL,
    publicDir: 'public',
    cacheDir: './node_modules/.vite-cache',
    optimizeDeps: {
      include: [
        'react',
        'react-dom',
        'react-router',
        'axios',
        'lodash',
        'dayjs',
      ],
      exclude: [],
      force: false,
    },
    build: {
      outDir: 'dist',
      assetsDir: 'assets',
      assetsInlineLimit: 4096,
      experimentalMinChunkSize: 30 * 1024,
      chunkSizeWarningLimit: 1000,
      rollupOptions: {
        onwarn(warning, warn) {
          if (warning.code === 'EMPTY_BUNDLE') {
            return;
          }
          warn(warning);
        },
        output: {
          manualChunks(id) {
            // if (id.includes('src/components')) {
            //   return 'components';
            // }

            if (id.includes('src/locales/') && id.endsWith('.ts')) {
              const match = id.match(/src\/locales\/([^/]+)\.ts$/);
              if (match) {
                return `locale-${match[1]}`;
              }
            }

            if (id.includes('node_modules')) {
              if (id.includes('node_modules/d3')) {
                return 'd3';
              }
              if (id.includes('node_modules/ajv')) {
                return 'ajv';
              }
              if (id.includes('node_modules/@antv')) {
                return 'antv';
              }
              const name = id
                .toString()
                .split('node_modules/')[1]
                .split('/')[0]
                .toString();
              if (['lodash', 'dayjs', 'date-fns', 'axios'].includes(name)) {
                return 'utils';
              }
              if (['@xmldom', 'xmlbuilder '].includes(name)) {
                return 'xml-js';
              }
              return name;
            }
          },
          chunkFileNames: 'chunk/js/[name]-[hash].js',
          entryFileNames: 'entry/js/[name]-[hash].js',
          assetFileNames: 'assets/[ext]/[name]-[hash].[ext]',
        },
        plugins: [],
        treeshake: true,
      },
      minify: resolveMinify(env.VITE_MINIFY),
      terserOptions: {
        compress: {
          drop_console: true, // delete console
          drop_debugger: true, // delete debugger
          pure_funcs: ['console.log'],
        },
        mangle: {
          // properties: {
          //   regex: /^_/,
          // },
          properties: false,
        },
        format: {
          comments: false, // Delete comments
        },
      },
      sourcemap: env.VITE_BUILD_SOURCEMAP !== 'false',
      cssCodeSplit: true,
      target: 'es2015',
    },
    esbuild: {
      tsconfigRaw: {
        compilerOptions: {
          strict: false,
          noImplicitAny: false,
          skipLibCheck: true,
        },
      },
    },
    entries: ['./src/main.tsx'],
  };
});
