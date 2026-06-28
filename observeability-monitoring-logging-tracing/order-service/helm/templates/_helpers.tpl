{{/*
Expand the name of the chart.
*/}}
{{- define "order-service.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "order-service.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "order-service.labels" -}}
app: {{ include "order-service.fullname" . }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — MUST stay stable across releases since the
Deployment selector, HPA scaleTargetRef, and Prometheus Adapter's
pod-label-based queries all key off "app".
*/}}
{{- define "order-service.selectorLabels" -}}
app: {{ include "order-service.fullname" . }}
{{- end }}

{{/*
Service account name to use
*/}}
{{- define "order-service.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "order-service.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
