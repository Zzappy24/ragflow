{{- define "ragflow-task-executor.fullname" -}}
{{- printf "%s-task-executor" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ragflow-task-executor.labels" -}}
app.kubernetes.io/name: ragflow-task-executor
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: task-executor
app.kubernetes.io/part-of: ragflow
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "ragflow-task-executor.selectorLabels" -}}
app.kubernetes.io/name: ragflow-task-executor
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ragflow-task-executor.image" -}}
{{- $g := .Values.global | default dict -}}
{{- if and $g.imageRegistry $g.imageRepository -}}
{{ $g.imageRegistry }}/{{ $g.imageRepository }}:{{ default .Values.image.tag $g.imageTag }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}
