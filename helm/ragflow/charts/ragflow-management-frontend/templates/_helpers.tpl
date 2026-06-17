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

{{- define "ragflow-management-frontend.image" -}}
{{- $g := .Values.global | default dict -}}
{{- if and $g.imageRegistry $g.imageRepository -}}
{{ $g.imageRegistry }}/{{ $g.imageRepository }}:{{ default .Values.image.tag $g.imageTag }}
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
  root /ragflow/management/web/dist;
  index index.html;

  location /assets/ {
    expires 30d;
    add_header Cache-Control "public, immutable";
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

  location / {
    try_files $uri $uri/ /index.html;
    add_header Cache-Control "no-cache, no-store, must-revalidate";
  }

  location = /healthz {
    access_log off;
    return 200 'ok';
  }
}
{{- end -}}