{{- define "ragflow-api.fullname" -}}
{{- printf "%s-api" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ragflow-api.labels" -}}
app.kubernetes.io/name: ragflow-api
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: api
app.kubernetes.io/part-of: ragflow
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "ragflow-api.selectorLabels" -}}
app.kubernetes.io/name: ragflow-api
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ragflow-api.image" -}}
{{- $g := .Values.global | default dict -}}
{{- $reg := $g.imageRegistry | default .Values.image.repository | toString -}}
{{- $repo := $g.imageRepository | default "" | toString -}}
{{- $tag := $g.imageTag | default .Values.image.tag | toString -}}
{{- if and $g.imageRegistry $g.imageRepository -}}
{{ $reg }}/{{ $repo }}:{{ $tag }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}
