{{/* =========================================================================
     Common helpers — used by every sub-chart and template in this umbrella.
     ========================================================================= */}}

{{/*
  Fully-qualified release name. Pads with the chart name so two installs of
  this umbrella in different namespaces (staging/prod) never collide.
*/}}
{{- define "ragflow.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{ .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{ printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end -}}
{{- end -}}

{{/*
  Standard labels stamped on every resource. `app.kubernetes.io/*` is the
  K8s recommended label set — Calico NetworkPolicies and Prometheus selectors
  rely on these, so don't drop any.
*/}}
{{- define "ragflow.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: ragflow
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/*
  Selector labels — subset of labels above. Used in Service `selector` and
  Deployment `matchLabels`. Must be IMMUTABLE across upgrades (changing them
  breaks rolling updates).
*/}}
{{- define "ragflow.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
  Container image reference, assembled from global registry/repo/tag.
  Sub-charts pass `.Values.global` and the component name (e.g. "api").
*/}}
{{- define "ragflow.image" -}}
{{- $g := .Values.global -}}
{{ $g.imageRegistry }}/{{ $g.imageRepository }}:{{ $g.imageTag }}
{{- end -}}

{{/*
  Common pod-level securityContext.
  In Phase 0 we keep it permissive: the upstream RAGFlow Dockerfile builds
  the venv as root and chmods nothing, so runAsUser:10001 hits "bad
  interpreter: Permission denied" on /ragflow/.venv/bin/python. Fixing
  that requires either a custom Dockerfile (chown -R) or initContainer
  fixup — both are Phase 1 hardening tasks.
  Toggle .Values.security.restricted=true once the image is fork-built
  with the right ownership.
*/}}
{{- define "ragflow.podSecurityContext" -}}
{{- $sec := default dict .Values.security -}}
{{- $g := default dict .Values.global -}}
{{- $gsec := default dict $g.security -}}
{{- $restricted := or $sec.restricted $gsec.restricted -}}
{{- if $restricted }}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- else }}
fsGroup: 0
{{- end }}
{{- end -}}

{{- define "ragflow.containerSecurityContext" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
readOnlyRootFilesystem: true
{{- end -}}
