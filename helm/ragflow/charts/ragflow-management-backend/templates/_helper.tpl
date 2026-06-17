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

{{- define "ragflow-management-backend.image" -}}
{{- $g := .Values.global | default dict -}}
{{- if and $g.imageRegistry $g.imageRepository -}}
{{ $g.imageRegistry }}/{{ $g.imageRepository }}:{{ default .Values.image.tag $g.imageTag }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}