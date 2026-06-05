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