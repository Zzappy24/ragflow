{{- define "ragflow-management-frontend.fullname" -}}
{{- printf "%s-management-frontend" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ragflow-management-frontend.labels" -}}
app.kubernetes.io/name: ragflow-management-frontend
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: management-frontend
app.kubernetes.io/part-of: ragflow
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "ragflow-management-frontend.selectorLabels" -}}
app.kubernetes.io/name: ragflow-management-frontend
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- /*
  CUSTOM B2B SaaS — same repositorySuffix pattern as the management-
  backend subchart: with global.imageRepository=data/ragflow and
  .Values.image.repositorySuffix=-mgmt-frontend the rendered image
  becomes data/ragflow-mgmt-frontend:<global.imageTag>. Without a
  suffix the chart falls through to single-fat-image mode.
*/}}
{{- define "ragflow-management-frontend.image" -}}
{{- $g := .Values.global | default dict -}}
{{- if and $g.imageRegistry $g.imageRepository -}}
{{- $repo := $g.imageRepository -}}
{{- with .Values.image.repositorySuffix -}}
{{- $repo = printf "%s%s" $repo . -}}
{{- end -}}
{{- /* CUSTOM B2B SaaS — subchart tag prioritised over global; see
   mgmt-backend helper for rationale. */ -}}
{{- $g.imageRegistry -}}/{{- $repo -}}:{{- default $g.imageTag .Values.image.tag -}}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}

{{- define "ragflow-management-frontend.hostname" -}}
{{- if .Values.hostnameOverride -}}
{{- .Values.hostnameOverride -}}
{{- else -}}
{{- printf "ragflow-management.%s" (trimPrefix "ragflow." .Values.global.hostname) -}}
{{- end -}}
{{- end -}}

{{- define "ragflow-management-frontend.backendServiceName" -}}
{{- printf "%s-management-backend" .Release.Name -}}
{{- end -}}

{{- define "ragflow-management-frontend.nginxMainConfig" -}}
worker_processes auto;
pid /var/run/nginx.pid;

events {
  worker_connections 1024;
}

http {
  include /etc/nginx/mime.types;
  default_type application/octet-stream;
  sendfile on;
  keepalive_timeout 65;
  client_max_body_size 32m;

  gzip on;
  gzip_types text/plain text/css application/javascript application/json application/xml text/xml;

  include /etc/nginx/conf.d/ragflow.conf;
}
{{- end -}}

{{- define "ragflow-management-frontend.nginxVhostConfig" -}}
upstream management_backend {
  server ${MANAGEMENT_BACKEND_SERVICE}.${POD_NAMESPACE}.svc.cluster.local:${MANAGEMENT_BACKEND_PORT};
  keepalive 32;
}

server {
  listen 80;
  server_name _;
  # CUSTOM B2B SaaS — the slim mgmt-frontend image copies the built
  # SPA to /usr/share/nginx/html (standard nginx:alpine path), not to
  # /ragflow/management/web/dist as it used to in the fat image.
  root /usr/share/nginx/html;
  index index.html;

  # CUSTOM B2B SaaS — en-têtes de sécurité (parité avec le front principal,
  # audit 2026-09-06 / 2026-09-07). Le panel n'est JAMAIS embarqué dans un
  # iframe : anti-clickjacking strict. nginx n'hérite pas d'add_header dans
  # un bloc qui en déclare : redéclarés dans chaque location qui en a.
  add_header X-Frame-Options "DENY" always;
  add_header X-Content-Type-Options "nosniff" always;
  add_header Referrer-Policy "no-referrer" always;
  add_header Content-Security-Policy "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" always;

  location /assets/ {
    expires 30d;
    add_header Cache-Control "public, immutable";
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer" always;
    try_files $uri =404;
  }

  location /api/admin/ {
    proxy_pass http://management_backend;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 60s;
  }

  # La SPA vit sous /admin (basename du router React) : la racine ne
  # matche aucune route et rendait une page blanche.
  location = / {
    return 302 /admin/;
  }

  location / {
    try_files $uri $uri/ /index.html;
    add_header Cache-Control "no-cache, no-store, must-revalidate";
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Content-Security-Policy "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" always;
  }

  location = /healthz {
    access_log off;
    return 200 'ok';
  }
}
{{- end -}}