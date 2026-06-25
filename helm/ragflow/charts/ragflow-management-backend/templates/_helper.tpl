{{- define "ragflow-management-backend.fullname" -}}
{{- printf "%s-management-backend" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ragflow-management-backend.labels" -}}
app.kubernetes.io/name: ragflow-management-backend
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: management-backend
app.kubernetes.io/part-of: ragflow
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "ragflow-management-backend.selectorLabels" -}}
app.kubernetes.io/name: ragflow-management-backend
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- /*
  CUSTOM B2B SaaS — the management backend ships in its own slim image
  (Dockerfile.management) to avoid pulling the 10 GB main image just to
  serve the admin panel. To share the umbrella's global.imageRegistry +
  global.imageTag while pointing at a different repo, the subchart
  optionally appends `.Values.image.repositorySuffix` to the global repo.
  Example: global.imageRepository=data/ragflow + suffix=-mgmt →
  data/ragflow-mgmt. Without the suffix, falls back to the previous
  behaviour (single fat image).
*/}}
{{- define "ragflow-management-backend.image" -}}
{{- $g := .Values.global | default dict -}}
{{- if and $g.imageRegistry $g.imageRepository -}}
{{- $repo := $g.imageRepository -}}
{{- with .Values.image.repositorySuffix -}}
{{- $repo = printf "%s%s" $repo . -}}
{{- end -}}
{{- /* CUSTOM B2B SaaS — subchart tag prioritised over global to allow
   independent bumps of the mgmt image (own Dockerfile, own deps,
   own release cycle). Original behaviour was global-first. */ -}}
{{- $g.imageRegistry -}}/{{- $repo -}}:{{- default $g.imageTag .Values.image.tag -}}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}